import io
import json
import os
import warnings
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException

load_dotenv()

from backend.ingest import extract_text
from backend.rag import (
    GroqRequestError,
    LLMConfigurationError,
    VisionModelUnsupportedError,
    ask_question,
    reset_collection,
    store_chunks,
)
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

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_REQUEST_BYTES = 14 * 1024 * 1024
MAX_IMAGES = 3
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


def _validate_image(data: bytes, content_type: str | None) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Each image must be 10 MB or smaller.")

    mime_type = (content_type or "").lower()
    expected_format = SUPPORTED_IMAGE_TYPES.get(mime_type)
    if expected_format is None:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image type. Upload a JPEG, PNG, or WebP image.",
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_format = image.format
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail="The uploaded image is malformed or unsafe.") from exc

    if actual_format != expected_format:
        raise HTTPException(
            status_code=400,
            detail="The image content does not match its declared MIME type.",
        )
    return mime_type


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
    store_chunks(chunks, filename=file.filename)

    return {
        "status": "success",
        "chunks": len(chunks)
    }

@app.post("/query")
async def query(request: Request):
    images: list[tuple[bytes, str]] = []
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    if content_type == "application/json":
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="The JSON query body is malformed.")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="The query body must be a JSON object.")
        question = body.get("question")
    elif content_type == "multipart/form-data":
        try:
            form = await request.form(
                max_files=MAX_IMAGES,
                max_fields=1,
                max_part_size=64 * 1024,
            )
        except (MultiPartException, ValueError):
            raise HTTPException(status_code=400, detail="The multipart query body is malformed.")

        try:
            question = form.get("question")
            uploads = []
            for field_name, value in form.multi_items():
                if isinstance(value, StarletteUploadFile):
                    if field_name != "image":
                        raise HTTPException(
                            status_code=400,
                            detail="Use the 'image' field for image attachments.",
                        )
                    uploads.append(value)
                elif field_name == "image":
                    raise HTTPException(
                        status_code=400,
                        detail="The 'image' field must contain an uploaded file.",
                    )

            if len(uploads) > MAX_IMAGES:
                raise HTTPException(status_code=413, detail="A query can include at most 3 images.")

            total_image_bytes = 0
            for upload in uploads:
                data = await upload.read(MAX_IMAGE_BYTES + 1)
                mime_type = _validate_image(data, upload.content_type)
                total_image_bytes += len(data)
                if total_image_bytes > MAX_IMAGE_REQUEST_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The combined image size must be 14 MB or smaller.",
                    )
                images.append((data, mime_type))
        finally:
            await form.close()
    else:
        raise HTTPException(
            status_code=415,
            detail="Send a JSON body for text queries or multipart form data for image queries.",
        )

    if not isinstance(question, str) or not question.strip():
        raise HTTPException(status_code=422, detail="A non-empty question is required.")

    try:
        if images:
            response = ask_question(question.strip(), images)
        else:
            response = ask_question(question.strip())
        response.setdefault("suggested_questions", [])
        return response
    except VisionModelUnsupportedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GroqRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@app.post("/reset")
async def reset():
    reset_collection()
    return {"status": "success"}
