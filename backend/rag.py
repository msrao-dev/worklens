import chromadb

from sentence_transformers import SentenceTransformer
from ollama import chat

MODEL = SentenceTransformer(
    "BAAI/bge-small-en-v1.5"
)

client = chromadb.PersistentClient(
    path="./backend/chroma_db"
)

collection = client.get_or_create_collection(
    "employee_docs"
)

def store_chunks(chunks):

    embeddings = MODEL.encode(
        chunks
    ).tolist()

    ids = [
        f"chunk_{i}"
        for i in range(len(chunks))
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings
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
        model="qwen2.5:7b",
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