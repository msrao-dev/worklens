import os
import hashlib
import chromadb
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = os.getenv("CHROMA_PATH", "./backend/chroma_db")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# Provider and model routing
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

MODEL = SentenceTransformer(EMBEDDING_MODEL_NAME)
client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_or_create_collection("employee_docs")


class Citation(BaseModel):
    chunk_id: str
    document_name: str | None = None
    chunk_index: int | None = None
    page_number: int | None = None
    text: str


class AnswerResponse(BaseModel):
    answer: str
    citations: list[Citation]
    insufficient_evidence: bool


def generate_answer(prompt: str) -> str:
    """Dispatches the prompt to the configured provider using its dedicated model."""
    if LLM_PROVIDER == "groq":
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY environment variable is not set.")
        
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
        
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2
        )
        return completion.choices[0].message.content

    # Default: Ollama
    from ollama import chat
    response = chat(
        model=OLLAMA_MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    return response.message.content

def store_chunks(chunks, filename="doc", chunk_metadata=None):
    if chunk_metadata is not None and len(chunk_metadata) != len(chunks):
        raise ValueError("chunk_metadata must have one item per chunk")

    if not chunks:
        collection.delete(where={"source": filename})
        return

    embeddings = MODEL.encode(chunks).tolist()
    document_key = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]
    ids = [f"{document_key}_{i}" for i in range(len(chunks))]
    metadatas = []
    for i in range(len(chunks)):
        metadata = {"source": filename, "chunk_index": i}
        if chunk_metadata is not None:
            metadata.update(
                {
                    key: value
                    for key, value in chunk_metadata[i].items()
                    if value is not None
                }
            )
            metadata["source"] = filename
            metadata["chunk_index"] = i
        metadatas.append(metadata)

    collection.delete(where={"source": filename})
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas
    )


def ask_question(question: str) -> AnswerResponse:
    query_embedding = MODEL.encode(question).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=5
    )

    docs = results.get("documents") or [[]]
    ids = results.get("ids") or [[]]
    metadatas = results.get("metadatas") or [[]]
    retrieved_docs = docs[0] if docs else []
    retrieved_ids = ids[0] if ids else []
    retrieved_metadatas = metadatas[0] if metadatas else []
    citations = []
    for index, text in enumerate(retrieved_docs):
        if not isinstance(text, str) or not text.strip():
            continue
        if index >= len(retrieved_ids) or not retrieved_ids[index]:
            continue

        metadata = (
            retrieved_metadatas[index]
            if index < len(retrieved_metadatas) and retrieved_metadatas[index]
            else {}
        )
        citations.append(
            Citation(
                chunk_id=retrieved_ids[index],
                document_name=metadata.get("source"),
                chunk_index=metadata.get("chunk_index"),
                page_number=metadata.get("page_number"),
                text=text,
            )
        )

    if not citations:
        return AnswerResponse(
            answer="Information not found in the available knowledge base.",
            citations=[],
            insufficient_evidence=True,
        )

    context = "\n\n".join(citation.text for citation in citations)
    prompt = f"""
Answer ONLY from context.

If not found say:
Information not found.

Context:
{context}

Question:
{question}
"""

    answer_text = generate_answer(prompt)

    return AnswerResponse(
        answer=answer_text,
        citations=citations,
        insufficient_evidence=False,
    )

def reset_collection():
    global collection
    client.delete_collection("employee_docs")
    collection = client.get_or_create_collection("employee_docs")