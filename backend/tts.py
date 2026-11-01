import hashlib
import io
import logging
import os
import re
import threading
import time
import wave
from typing import Optional

logger = logging.getLogger(__name__)

VOICES_DIR = os.getenv("VOICES_DIR", "./backend/voices")
CACHE_DIR = os.getenv("TTS_CACHE_DIR", "./backend/cache/audio")
os.makedirs(CACHE_DIR, exist_ok=True)

VOICE_CONFIGS = {
    "amy": {
        "model": "en_US-amy-medium.onnx",
        "config": "en_US-amy-medium.onnx.json",
    },
    "ryan": {
        "model": "en_US-ryan-high.onnx",
        "config": "en_US-ryan-high.onnx.json",
    },
}

_VOICE_INSTANCES = {}
_SYNTHESIS_LOCK = threading.Lock()


def sanitize_text_for_speech(text: str) -> str:
    """Strips markdown syntax, headers, code, and HTML tags for smooth vocal delivery."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}", "", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_into_sentence_chunks(text: str, max_chunk_chars: int = 180) -> list[str]:
    """Splits text along sentence boundaries to keep individual synthesis steps fast."""
    raw_sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for sent in raw_sentences:
        sent = sent.strip()
        if not sent:
            continue

        if current_len + len(sent) > max_chunk_chars and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = [sent]
            current_len = len(sent)
        else:
            current_chunk.append(sent)
            current_len += len(sent) + 1

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks if chunks else [text]


def get_voice_instance(voice_name: str):
    name = voice_name.lower().strip()
    if name not in VOICE_CONFIGS:
        name = "amy"

    if name not in _VOICE_INSTANCES:
        from piper import PiperVoice

        cfg = VOICE_CONFIGS[name]
        model_path = os.path.join(VOICES_DIR, cfg["model"])
        config_path = os.path.join(VOICES_DIR, cfg["config"])

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Voice model not found at {model_path}")

        logger.info("Loading Piper voice model '%s' from disk...", name)
        _VOICE_INSTANCES[name] = PiperVoice.load(model_path, config_path=config_path)

    return _VOICE_INSTANCES[name]


def get_cache_path(voice_name: str, sanitized_text: str) -> str:
    raw = f"{voice_name}:{sanitized_text}".encode("utf-8")
    key = hashlib.sha256(raw).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.wav")


def synthesize_speech(text: str, voice_name: str = "amy") -> tuple[bytes, str]:
    """Synthesizes text in sentence chunks, merges the frames, and caches the result."""
    clean_text = sanitize_text_for_speech(text)
    if not clean_text:
        clean_text = "Nothing to read."

    cached_file = get_cache_path(voice_name, clean_text)
    if os.path.exists(cached_file):
        try:
            with open(cached_file, "rb") as f:
                return f.read(), cached_file
        except Exception:
            logger.warning("Failed reading cached audio file: %s. Re-synthesizing.", cached_file)

    voice = get_voice_instance(voice_name)
    chunks = split_into_sentence_chunks(clean_text, max_chunk_chars=180)

    combined_frames = bytearray()
    wav_params = None

    for chunk in chunks:
        chunk_buffer = io.BytesIO()
        with _SYNTHESIS_LOCK:
            with wave.open(chunk_buffer, "wb") as chunk_wav:
                voice.synthesize_wav(chunk, chunk_wav)

        chunk_buffer.seek(0)
        with wave.open(chunk_buffer, "rb") as read_wav:
            if wav_params is None:
                wav_params = read_wav.getparams()
            combined_frames.extend(read_wav.readframes(read_wav.getnframes()))

    out_buffer = io.BytesIO()
    with wave.open(out_buffer, "wb") as merged_wav:
        merged_wav.setparams(wav_params)
        merged_wav.writeframes(combined_frames)

    wav_bytes = out_buffer.getvalue()

    try:
        with open(cached_file, "wb") as f:
            f.write(wav_bytes)
    except Exception as exc:
        logger.warning("Could not write audio to cache directory: %s", exc)

    return wav_bytes, cached_file


def cleanup_audio_cache():
    """Removes cached files older than 1 hour; trims cache below 50MB if it exceeds 100MB."""
    try:
        now = time.time()
        files = []

        for entry in os.scandir(CACHE_DIR):
            if entry.is_file() and entry.name.endswith(".wav"):
                stat = entry.stat()
                if now - stat.st_mtime > 3600:
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
                else:
                    files.append((entry.path, stat.st_size, stat.st_mtime))

        total_size = sum(f[1] for f in files)
        max_bytes = 100 * 1024 * 1024
        target_bytes = 50 * 1024 * 1024

        if total_size > max_bytes:
            files.sort(key=lambda x: x[2])
            for path, size, _ in files:
                try:
                    os.remove(path)
                    total_size -= size
                    if total_size <= target_bytes:
                        break
                except OSError:
                    pass
    except Exception as exc:
        logger.warning("Audio cache pruning encountered an error: %s", exc)
