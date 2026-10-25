import os
import base64
import logging
import re
import chromadb
import uuid
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

CHROMA_PATH = os.getenv("CHROMA_PATH", "./backend/chroma_db")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# Provider and model routing
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_VISION_MODELS = {"qwen/qwen3.8-27b"}

MODEL = SentenceTransformer(EMBEDDING_MODEL_NAME)
client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_or_create_collection("employee_docs")

ImageInput = tuple[bytes, str]

GREETING_PATTERN = re.compile(
    r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening)|greetings|howdy|hi\s+there|hello\s+there)[!.,\s]*$",
    re.IGNORECASE,
)


class LLMConfigurationError(Exception):
    pass


class VisionModelUnsupportedError(Exception):
    pass


class GroqRequestError(Exception):
    pass


def is_greeting(text: str) -> bool:
    """Checks if the user input is a pure greeting or polite conversational opening."""
    return bool(GREETING_PATTERN.match(text.strip()))


def generate_answer(prompt: str, images: list[ImageInput] | None = None) -> str:
    """Dispatches the prompt to the configured provider using its dedicated model."""
    if images and LLM_PROVIDER != "groq":
        raise VisionModelUnsupportedError(
            "Image queries require LLM_PROVIDER=groq and a vision-capable Groq model."
        )

    if LLM_PROVIDER == "groq":
        if not GROQ_API_KEY:
            raise LLMConfigurationError("GROQ_API_KEY is not configured on the backend.")
        if images and GROQ_MODEL_NAME not in GROQ_VISION_MODELS:
            raise VisionModelUnsupportedError(
                "The configured Groq model does not support image input. "
                "Set GROQ_MODEL to qwen/qwen3.8-27b."
            )

        from groq import APIError, Groq

        groq_client = Groq(api_key=GROQ_API_KEY)

        content: str | list[dict[str, object]] = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for image_bytes, mime_type in images:
                image_data = base64.b64encode(image_bytes).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                })

        try:
            completion = groq_client.chat.completions.create(
                model=GROQ_MODEL_NAME,
                messages=[{"role": "user", "content": content}],
                temperature=0.3,
            )
        except APIError as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code == 401:
                message = "Groq authentication failed. Check the backend GROQ_API_KEY configuration."
            elif status_code == 429:
                message = "Groq rate limit reached. Please retry later."
            elif status_code == 400:
                message = (
                    "Groq rejected the request. Verify that the configured model is available "
                    "and supports this request."
                )
            else:
                message = "Groq could not process the request. Please retry later."
            raise GroqRequestError(message) from exc

        if completion.model:
            logger.info("Groq returned model %s", completion.model)
        return completion.choices[0].message.content

    # Default: Ollama
    from ollama import chat

    response = chat(
        model=OLLAMA_MODEL_NAME,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )
    return response.message.content


def store_chunks(chunks, filename="doc"):
    embeddings = MODEL.encode(chunks).tolist()

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
        metadatas=metadatas,
    )


def ask_question(question: str, images: list[ImageInput] | None = None):
    # Handle pure greetings dynamically via the LLM without searching vector DB
    if not images and is_greeting(question):
        greeting_prompt = f"""
You are WorkLens, an enterprise employee knowledge assistant.
The employee greeted you with: "{question}".

Respond professionally, warmly, and concisely (1 to 2 sentences).
Acknowledge the greeting courteously and proactively guide the user to ask any questions they have regarding the company employee handbook, such as leave policies, benefits, code of conduct, onboarding, IT assets, or reimbursement procedures.
Do not invent company policies. Keep the tone courteous, helpful, and professional.
"""
        model_reply = generate_answer(greeting_prompt)
        return {
            "answer": model_reply,
            "sources": [],
            "source_refs": [],
            "is_greeting": True,
        }

    query_embedding = MODEL.encode(question).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=5,
    )

    docs = results["documents"][0] if results["documents"] else []
    context = "\n\n".join(docs)
    metadatas = results.get("metadatas") or [[]]
    metadata = metadatas[0] if metadatas and metadatas[0] else []
    source_refs = []
    for index, doc in enumerate(docs):
        chunk_metadata = metadata[index] if index < len(metadata) and metadata[index] else {}
        source_refs.append({
            "source": chunk_metadata.get("source", "Knowledge Base"),
            "chunk_index": chunk_metadata.get("chunk_index"),
            "text": doc,
        })

    if images:
        prompt = f"""
Answer the user's question using both the image and the retrieved handbook context.

Company policy claims must be supported by the retrieved context. The image may support
visual observations, but it is not evidence of company policy. Clearly distinguish visual
observations from policy evidence. If the retrieved context does not answer a policy
question, say: Information not found.

Retrieved context:
{context}

Question:
{question}
"""
    else:
        prompt = f"""
Answer ONLY from the retrieved context.

If the answer is not present in the context, say:
Information not found.

Context:
{context}

Question:
{question}
"""

    answer_text = generate_answer(prompt, images) if images else generate_answer(prompt)

    return {
        "answer": answer_text,
        "sources": docs,
        "source_refs": source_refs,
        "is_greeting": False,
    }


def reset_collection():
    global collection
    client.delete_collection("employee_docs")
    collection = client.get_or_create_collection("employee_docs")