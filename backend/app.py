import os
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from backend.ingest import extract_text
from backend.rag import store_chunks, ask_question
from langchain_text_splitters import RecursiveCharacterTextSplitter

app = FastAPI()

# Configurable CORS origins
ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS", 
    "http://localhost:4200,http://127.0.0.1:4200"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./backend/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/")
def root():
    return {
        "message": "Employee Knowledge Assistant API",
        "swagger": "/docs"
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    filepath = os.path.join(UPLOAD_DIR, file.filename)

    with open(filepath, "wb") as f:
        f.write(await file.read())

    text = extract_text(filepath)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )

    chunks = splitter.split_text(text)
    store_chunks(chunks)

    return {
        "status": "success",
        "chunks": len(chunks)
    }

@app.post("/query")
async def query(body: dict):
    return ask_question(body["question"])