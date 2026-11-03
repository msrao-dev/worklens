"""Shared setup for the evaluation suite: paths, environment and Groq retries."""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
GOLDEN_FILE = EVALUATION_DIR / "golden_questions.json"
THRESHOLDS_FILE = EVALUATION_DIR / "thresholds.json"
REPORT_DIR = EVALUATION_DIR / "reports"

# Backend paths such as CHROMA_PATH and UPLOAD_DIR are relative to the repo root.
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

# Seconds to wait before each retry when Groq returns 429.
GROQ_RETRY_DELAYS = (15, 30, 60)


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def judge_model():
    """Model that scores answers; defaults to the model the app answers with."""
    return os.getenv("EVAL_JUDGE_MODEL") or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")


def ask_with_retry(question, images=None):
    """Call the real RAG pipeline, retrying only on Groq rate limits."""
    from backend.rag import GroqRequestError, ask_question

    for delay in (*GROQ_RETRY_DELAYS, None):
        try:
            response = ask_question(question, images=images)
        except GroqRequestError as exc:
            if delay is None or "rate limit" not in str(exc).lower():
                raise
            print(f"  Groq rate limit reached, retrying in {delay}s")
            time.sleep(delay)
            continue

        if not isinstance(response, dict):
            raise TypeError("ask_question() did not return a dictionary")
        return response
