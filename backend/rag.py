import os
import base64
import json
import logging
import re
import chromadb
import uuid
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from backend.ingest import IndexedChunk

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
                temperature=0.2,
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


def contextualize_question(question: str, history: list[dict[str, str]]) -> str:
    """Rewrites a follow-up query into a standalone retrieval question using conversation history."""
    if not history:
        return question

    history_lines = []
    for turn in history[-4:]:
        role = "Employee" if turn.get("role") == "user" else "Assistant"
        history_lines.append(f"{role}: {turn.get('text', '')}")

    formatted_history = "\n".join(history_lines)

    prompt = f"""Given the following conversation history between an Employee and an Assistant, and a follow-up query from the Employee, rewrite the follow-up query into a clear, standalone search question for retrieving handbook policies.
Do NOT answer the question. Only output the rewritten question without any preamble.

Conversation History:
{formatted_history}

Follow-up Query: {question}

Standalone Question:"""

    try:
        rewritten = generate_answer(prompt).strip().strip('"').strip("'")
        return rewritten if rewritten else question
    except Exception as e:
        logger.warning("Failed to contextualize question: %s", e)
        return question


def generate_suggested_questions(question: str, answer: str, context: str) -> list[str]:
    prompt = f"""
Suggest 3 or 4 concise follow-up questions for the user.

Use only information explicitly present in the retrieved context. Questions must be
relevant to the user's question and answer, and must not assume or introduce company
policies or facts. If the context does not support useful follow-up questions, return [].
Return only a JSON array of strings, with no markdown or additional text.

Retrieved context:
{context}

User's question:
{question}

Answer:
{answer}
"""
    output = generate_answer(prompt)
    try:
        suggestions = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Suggested-question generation returned invalid JSON.")
        return []

    if not isinstance(suggestions, list) or not 3 <= len(suggestions) <= 4:
        logger.warning("Suggested-question generation returned an invalid number of questions.")
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for suggestion in suggestions:
        if not isinstance(suggestion, str):
            logger.warning("Suggested-question generation returned a non-string question.")
            return []
        text = suggestion.strip()
        key = text.casefold()
        if not text or len(text) > 120 or not text.endswith("?") or key in seen:
            logger.warning("Suggested-question generation returned an invalid question.")
            return []
        normalized.append(text)
        seen.add(key)
    return normalized


def store_chunks(chunks: list[IndexedChunk], filename: str = "doc") -> None:
    documents = [chunk["text"] for chunk in chunks]
    embeddings = MODEL.encode(documents).tolist()

    ids = [
        f"{filename}_{i}_{uuid.uuid4().hex[:8]}"
        for i in range(len(documents))
    ]

    metadatas = []
    for index, chunk in enumerate(chunks):
        metadata: dict[str, str | int] = {
            "source": filename,
            "chunk_index": index,
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
        }
        if chunk["page_number"] is not None:
            metadata["page_number"] = chunk["page_number"]
        metadatas.append(metadata)

    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )


def ask_question(
    question: str,
    images: list[ImageInput] | None = None,
    history: list[dict[str, str]] | None = None,
):
    history = history or []

    # Handle pure greetings dynamically via LLM without vector lookup
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
            "suggested_questions": [],
            "is_greeting": True,
        }

    # Resolve follow-ups like "give me TLDR", "explain more", "what about sick leave?"
    retrieval_query = question
    if history and not images:
        retrieval_query = contextualize_question(question, history)
        logger.info("Contextualized query '%s' -> '%s'", question, retrieval_query)

    query_embedding = MODEL.encode(retrieval_query).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=5,
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0] if results["documents"] else []
    distance_results = results.get("distances")
    distances = distance_results[0] if distance_results else []
    context = "\n\n".join(
        f"Retrieved passage {index + 1}:\n{doc}"
        for index, doc in enumerate(docs)
    )
    for index, doc in enumerate(docs):
        distance = distances[index] if index < len(distances) else None
        logger.info(
            "Chroma result rank=%d distance=%s characters=%d",
            index + 1,
            distance,
            len(doc),
        )

    metadatas = results.get("metadatas") or [[]]
    metadata = metadatas[0] if metadatas and metadatas[0] else []
    source_refs = []
    for index, doc in enumerate(docs):
        chunk_metadata = metadata[index] if index < len(metadata) and metadata[index] else {}
        source_refs.append({
            "source": chunk_metadata.get("source", "Knowledge Base"),
            "chunk_index": chunk_metadata.get("chunk_index"),
            "page_number": chunk_metadata.get("page_number"),
            "line_start": chunk_metadata.get("line_start"),
            "line_end": chunk_metadata.get("line_end"),
            "text": doc,
        })

    # Format previous turns for the prompt context
    history_context = ""
    if history:
        history_lines = []
        for turn in history[-4:]:
            role = "Employee" if turn.get("role") == "user" else "Assistant"
            history_lines.append(f"{role}: {turn.get('text', '')}")
        history_context = f"\nRecent Conversation History:\n" + "\n".join(history_lines) + "\n"

    if images:
        prompt = f"""
You are WorkLens, an enterprise knowledge assistant answering queries based on company policy documents.
{history_context}
Answer the user's question using both the image and the retrieved handbook context.

Read all retrieved passages; the retrieval order does not mean that later passages are
irrelevant. If any passage supports an answer, answer from it. Company policy claims must
be supported by the retrieved context. The image may support visual observations, but it
is not evidence of company policy. Clearly distinguish visual observations from policy
evidence. Only if none of the retrieved passages contains relevant information, respond
exactly: Information not found.

Retrieved Context:
{context}

Question:
{question}

Instructions:
1. Answer the question using the retrieved context, previous conversation turns, and any provided images.
2. Company policy claims must be grounded in the retrieved context. If specific details (e.g. daily-rate formulas) are not fully stated in the text, present all relevant rules, bases (e.g., basic salary), and limits mentioned in the context, and note what remains unspecified.
3. Distinguish visual observations from official policy statements.
4. Only return "Information not found." if the retrieved context is completely irrelevant to the question.
"""
    else:
        prompt = f"""
You are WorkLens, an enterprise knowledge assistant answering queries based on company policy documents.
{history_context}
Answer using the retrieved passages below. Read all passages; the retrieval order does not
mean that later passages are irrelevant. If any passage supports an answer, answer from
that information and do not reject it merely because other passages are unrelated. Do not
infer company policy beyond what the passages state.

Only if none of the passages contains information relevant to the question, respond
exactly: Information not found.

Retrieved Context:
{context}

Question:
{question}

Instructions:
1. Answer the question based on the retrieved context and recent conversation history above. If the user asks for a summary, TLDR, or clarification of a previous response, focus directly on the pertinent topic previously discussed using the handbook context.
2. Synthesize all pertinent rules, eligibility conditions, calculation bases (e.g., basic salary vs. allowances), and caps given in the text.
3. If an exact mathematical formula is not explicitly detailed in the text, explain the calculation rules that ARE provided (e.g., carryover thresholds, maximum encashable days, pay components) and state clearly if any granular divisor or formula detail is not mentioned.
4. Do not invent unmentioned terms or policies.
5. Only return "Information not found." if the retrieved context contains no relevant information to address the question.
"""

    answer_text = generate_answer(prompt, images) if images else generate_answer(prompt)
    try:
        suggested_questions = generate_suggested_questions(question, answer_text, context)
    except Exception:
        logger.exception("Could not generate suggested questions.")
        suggested_questions = []

    return {
        "answer": answer_text,
        "sources": docs,
        "source_refs": source_refs,
        "suggested_questions": suggested_questions,
        "is_greeting": False,
    }


def reset_collection():
    global collection
    client.delete_collection("employee_docs")
    collection = client.get_or_create_collection("employee_docs")