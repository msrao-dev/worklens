import os
import chromadb
import uuid
from sentence_transformers import SentenceTransformer
from ollama import chat

CHROMA_PATH = os.getenv("CHROMA_PATH", "./backend/chroma_db")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

MODEL = SentenceTransformer(EMBEDDING_MODEL_NAME)
client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_or_create_collection("employee_docs")

def store_chunks(chunks, filename="doc"):

    embeddings = MODEL.encode(
        chunks
    ).tolist()

    ids = [
        f"{filename}_{i}_{uuid.uuid4().hex[:8]}"
        for i in range(len(chunks))
    ]

    metadatas = [
        {"source": filename, "chunk_index": i}
        for i in range(len(chunks))
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas
    )

def ask_question(question):

    query_embedding = MODEL.encode(
        question
    ).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=5
    )

    docs = results["documents"][0]

    context = "\n\n".join(docs)

    prompt = f"""
Answer ONLY from context.

If not found say:
Information not found.

Context:
{context}

Question:
{question}
"""

    response = chat(
        model=OLLAMA_MODEL_NAME,
        messages=[
            {
                "role":"user",
                "content":prompt
            }
        ]
    )

    return {
        "answer":
        response.message.content,
        "sources":
        docs
    }