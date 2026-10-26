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
    # Strip HTML tags like <mark>, <code>, <p>, <li>
    text = re.sub(r"<[^>]+>", " ", text)
    # Strip markdown links [label](url) -> label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Strip bold/italic formatting (*, **, _, __)
    text = re.sub(r"[*_]{1,3}", "", text)
    # Strip markdown headers (#, ##, etc.)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Strip bullet indicators and list numbers
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Strip inline code formatting
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Collapse multiple whitespaces/newlines
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
    """Returns (wav_bytes, file_path) using disk caching."""
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
    buffer = io.BytesIO()

    # Piper C/espeak engine requires synchronization under concurrent calls
    with _SYNTHESIS_LOCK:
        with wave.open(buffer, "wb") as wav_file:
            voice.synthesize_wav(clean_text, wav_file)

    wav_bytes = buffer.getvalue()

    # Save to ephemeral cache
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
                # Rule 1: Delete files older than 1 hour (3600 seconds)
                if now - stat.st_mtime > 3600:
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
                else:
                    files.append((entry.path, stat.st_size, stat.st_mtime))

        # Rule 2: If size > 100MB, delete oldest files until under 50MB
        total_size = sum(f[1] for f in files)
        max_bytes = 100 * 1024 * 1024
        target_bytes = 50 * 1024 * 1024

        if total_size > max_bytes:
            # Sort by modification time ascending (oldest first)
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
