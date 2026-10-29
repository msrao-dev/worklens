import io
import json
import os
from typing import Optional
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException
from pydantic import BaseModel
from backend.tts import synthesize_speech, cleanup_audio_cache
from backend.guardrails import validate_input

load_dotenv()

from backend.db import (
    get_db_connection,
    hash_password,
    init_db_and_seed_hr,
    verify_password,
)
from backend.auth import (
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    create_access_token,
    get_current_user,
    require_hr_user,
)
from backend.ingest import IndexedChunk, extract_text_units
from backend.rag import (
    GroqRequestError,
    LLMConfigurationError,
    VisionModelUnsupportedError,
    ask_question,
    reset_collection,
    store_chunks,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter


def _split_text_units(text_units: list[tuple[int | None, str]]) -> list[IndexedChunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        add_start_index=True,
    )
    chunks: list[IndexedChunk] = []

    for page_number, text in text_units:
        if not text.strip():
            continue

        for document in splitter.create_documents([text]):
            chunk_text = document.page_content
            start_index = document.metadata.get("start_index")
            if (
                not chunk_text
                or not isinstance(start_index, int)
                or start_index < 0
                or start_index + len(chunk_text) > len(text)
            ):
                raise RuntimeError("Could not determine a document chunk's source position.")

            line_start = text.count("\n", 0, start_index) + 1
            line_end = line_start + chunk_text.rstrip("\r\n").count("\n")
            chunks.append({
                "text": chunk_text,
                "page_number": page_number,
                "line_start": line_start,
                "line_end": line_end,
            })

    return chunks


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = "amy"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_and_seed_hr()
    yield


app = FastAPI(lifespan=lifespan)

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


# --- Auth Endpoints ---

@app.post("/auth/login")
def login(payload: LoginRequest):
    email = payload.email.strip()
    password = payload.password.strip()

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password are required.",
        )

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT id, email, pw_hash, role, is_active FROM users WHERE email = ?",
            (email,),
        )
        user = cursor.fetchone()
        if not user or not verify_password(password, user["pw_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
            )

        if not user["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is deactivated.",
            )

        token = create_access_token({"sub": user["email"], "role": user["role"]})
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": user["role"],
            },
        }
    finally:
        conn.close()


@app.post("/auth/register")
def register(payload: RegisterRequest, _current_user: dict = Depends(require_hr_user)):
    email = payload.email.strip()
    password = payload.password.strip()
    role = (payload.role or "EMPLOYEE").strip().upper()

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and password are required.",
        )

    if role not in ("HR", "EMPLOYEE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be either 'HR' or 'EMPLOYEE'.",
        )

    conn = get_db_connection()
    try:
        cursor = conn.execute("SELECT id FROM users WHERE email = ?", (email,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            )

        now_iso = datetime.now(timezone.utc).isoformat().split("+")[0]
        hashed = hash_password(password)

        with conn:
            cursor = conn.execute(
                """
                INSERT INTO users (email, pw_hash, role, is_active, created_on)
                VALUES (?, ?, ?, 1, ?)
                """,
                (email, hashed, role, now_iso),
            )
            new_id = cursor.lastrowid

        return {
            "status": "success",
            "message": "User registered successfully.",
            "user": {
                "id": new_id,
                "email": email,
                "role": role,
            },
        }
    finally:
        conn.close()


@app.post("/auth/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    new_pw = payload.new_password.strip()
    retype_pw = payload.retype_new_password.strip()

    if not new_pw or not retype_pw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both password fields are required.",
        )

    if new_pw != retype_pw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match.",
        )

    conn = get_db_connection()
    try:
        hashed = hash_password(new_pw)
        with conn:
            conn.execute(
                "UPDATE users SET pw_hash = ? WHERE id = ?",
                (hashed, current_user["id"]),
            )
        return {"status": "success", "message": "Password updated successfully."}
    finally:
        conn.close()


# --- Application Endpoints ---

@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    _current_user: dict = Depends(require_hr_user),
):
    if not file.filename:
        raise HTTPException(status_code=422, detail="A filename is required.")

    filepath = os.path.join(UPLOAD_DIR, file.filename)

    with open(filepath, "wb") as f:
        f.write(await file.read())

    chunks = _split_text_units(extract_text_units(filepath))
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail="No extractable text was found in the uploaded document.",
        )
    store_chunks(chunks, filename=file.filename)

    return {
        "status": "success",
        "chunks": len(chunks)
    }


@app.post("/query")
async def query(
    request: Request,
    _current_user: dict = Depends(get_current_user),
):
    images: list[tuple[bytes, str]] = []
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    history: list[dict[str, str]] = []

    if content_type == "application/json":
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="The JSON query body is malformed.")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="The query body must be a JSON object.")
        question = body.get("question")
        history_raw = body.get("history")
        if isinstance(history_raw, list):
            history = [h for h in history_raw if isinstance(h, dict) and "text" in h and "role" in h]

    elif content_type == "multipart/form-data":
        try:
            form = await request.form(
                max_files=MAX_IMAGES,
                max_fields=5,
                max_part_size=64 * 1024,
            )
        except (MultiPartException, ValueError):
            raise HTTPException(status_code=400, detail="The multipart query body is malformed.")

        try:
            question = form.get("question")
            history_field = form.get("history")
            if history_field:
                try:
                    parsed_history = json.loads(str(history_field))
                    if isinstance(parsed_history, list):
                        history = [h for h in parsed_history if isinstance(h, dict) and "text" in h and "role" in h]
                except Exception:
                    history = []

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

    try:
        question = validate_input(question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        response = ask_question(question.strip(), images if images else None, history=history)
        response.setdefault("suggested_questions", [])
        return response
    except VisionModelUnsupportedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GroqRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/reset")
async def reset(_current_user: dict = Depends(require_hr_user)):
    reset_collection()
    return {"status": "success"}

@app.post("/audio/tts")
def text_to_speech(
    payload: TTSRequest,
    background_tasks: BackgroundTasks,
    _current_user: dict = Depends(get_current_user),
):
    text = payload.text.strip()
    voice = (payload.voice or "amy").strip().lower()

    if not text:
        raise HTTPException(status_code=400, detail="Text payload is required.")

    if voice not in ("amy", "ryan"):
        voice = "amy"

    wav_bytes, _ = synthesize_speech(text, voice_name=voice)

    # Trigger post-response cleanup in the background
    background_tasks.add_task(cleanup_audio_cache)

    return Response(content=wav_bytes, media_type="audio/wav")