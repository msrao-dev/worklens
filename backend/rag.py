import hashlib
import math
import os
from collections.abc import Mapping
from typing import Any

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


def _read_max_distance() -> float | None:
    value = os.getenv("CHROMA_MAX_DISTANCE", "").strip()
    if not value:
        return None
    try:
        distance = float(value)
    except ValueError as exc:
        raise ValueError("CHROMA_MAX_DISTANCE must be a finite number") from exc
    if not math.isfinite(distance):
        raise ValueError("CHROMA_MAX_DISTANCE must be a finite number")
    return distance


MAX_CHROMA_DISTANCE = _read_max_distance()

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


def _collection_distance_space() -> str | None:
    configuration = getattr(collection, "configuration", None)
    if isinstance(configuration, Mapping):
        for index_name in ("hnsw", "spann"):
            index_config = configuration.get(index_name)
            if index_config is None:
                continue
            if not isinstance(index_config, Mapping):
                return None
            space = index_config.get("space", "l2")
            return space if isinstance(space, str) and space in {"l2", "cosine", "ip"} else None

    metadata = getattr(collection, "metadata", None)
    if isinstance(metadata, Mapping):
        space = metadata.get("hnsw:space")
        if isinstance(space, str) and space in {"l2", "cosine", "ip"}:
            return space
    return None


def _metadata_value(metadata, key: str, value_type: type, minimum: int | None = None):
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get(key)
    if not isinstance(value, value_type) or isinstance(value, bool):
        return None
    if minimum is not None and value < minimum:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _first_query_batch(results: Mapping[str, Any], field: str) -> list[Any]:
    batches = results.get(field)
    if not isinstance(batches, list) or not batches or not isinstance(batches[0], list):
        return []
    return batches[0]


def _insufficient_evidence(message: str) -> AnswerResponse:
    return AnswerResponse(
        answer=message,
        citations=[],
        insufficient_evidence=True,
    )


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
        n_results=5,
        include=["documents", "metadatas", "distances"],
    )

    retrieved_docs = _first_query_batch(results, "documents")
    retrieved_ids = _first_query_batch(results, "ids")
    retrieved_metadatas = _first_query_batch(results, "metadatas")
    retrieved_distances = _first_query_batch(results, "distances")

    if MAX_CHROMA_DISTANCE is None:
        return _insufficient_evidence(
            "Search relevance is not calibrated for this knowledge base, so I can't confirm that retrieved passages are relevant."
        )

    distance_space = _collection_distance_space()
    if distance_space is None:
        return _insufficient_evidence(
            "The knowledge base distance metric is unavailable, so retrieved passages can't be checked for relevance."
        )

    citations = []
    for index, text in enumerate(retrieved_docs):
        if not isinstance(text, str) or not text.strip():
            continue
        if index >= len(retrieved_ids) or not isinstance(retrieved_ids[index], str) or not retrieved_ids[index].strip():
            continue
        if index >= len(retrieved_distances):
            continue
        distance = retrieved_distances[index]
        if not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance):
            continue
        if distance > MAX_CHROMA_DISTANCE:
            continue

        metadata = (
            retrieved_metadatas[index]
            if index < len(retrieved_metadatas)
            else {}
        )
        citations.append(
            Citation(
                chunk_id=retrieved_ids[index],
                document_name=_metadata_value(metadata, "source", str),
                chunk_index=_metadata_value(metadata, "chunk_index", int, minimum=0),
                page_number=_metadata_value(metadata, "page_number", int, minimum=1),
                text=text,
            )
        )

    if not citations:
        return _insufficient_evidence(
            "Information not found in the available knowledge base with sufficient relevance."
        )

    context = "\n\n".join(citation.text for citation in citations)
    prompt = f"""
Answer using ONLY the evidence in the context below. Do not use prior knowledge,
make assumptions, or infer details that are not explicitly supported by it.
If the context does not support an answer, say exactly:
Information not found in the available knowledge base.

Do not claim that an unsupported detail is certain. The application will attach
citations only from the retrieved context; do not invent document names, pages,
or citation references.

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