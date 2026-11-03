"""Shared setup for the evaluation suite: paths, environment and Groq retries."""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
GOLDEN_FILE = EVALUATION_DIR / "golden_data_v1.json"
THRESHOLDS_FILE = EVALUATION_DIR / "thresholds.json"
REPORT_DIR = EVALUATION_DIR / "reports"

# Backend paths such as CHROMA_PATH and UPLOAD_DIR are relative to the repo root.
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

# The eval must never spend the live app's Groq quota, so it runs on its own key.
# backend.rag reads GROQ_API_KEY at import, which happens after this swap.
EVAL_GROQ_API_KEY = os.getenv("EVAL_GROQ_API_KEY", "").strip()
if EVAL_GROQ_API_KEY:
    os.environ["GROQ_API_KEY"] = EVAL_GROQ_API_KEY
else:
    os.environ.pop("GROQ_API_KEY", None)

# Index every policy PDF in documents/ into a Chroma folder of its own, so the
# local app's backend/chroma_db is never touched. backend reads both at import.
os.environ["UPLOAD_DIR"] = os.getenv("EVAL_DOCUMENTS_DIR", "./documents")
os.environ["CHROMA_PATH"] = os.getenv("EVAL_CHROMA_PATH", "./evaluation/chroma_db")

# Ask the app for shorter answers to save tokens; 0 turns the limit off.
ANSWER_MAX_WORDS = int(os.getenv("EVAL_ANSWER_MAX_WORDS", "100"))

# Seconds to wait before each retry when Groq returns 429.
GROQ_RETRY_DELAYS = (15, 30, 60)


def load_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def judge_model():
    """Model that scores answers; defaults to the model the app answers with."""
    return os.getenv("EVAL_JUDGE_MODEL") or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")


# Judge calls occasionally return invalid JSON; retry each one this many times in total.
JUDGE_ATTEMPTS = 3


def judge_model_args():
    """Generation settings for judge calls.

    900 output tokens stays under Groq's on-demand cap for qwen; reasoning models
    need more room, set via EVAL_JUDGE_MAX_TOKENS. gpt-oss reasons less, and so
    finishes its JSON sooner, with reasoning_effort=low.
    """
    args = {"max_tokens": int(os.getenv("EVAL_JUDGE_MAX_TOKENS", "900"))}
    if judge_model().startswith("openai/gpt-oss"):
        args["reasoning_effort"] = "low"
    return args


def ask_with_retry(question, images=None):
    """Call the real RAG pipeline, retrying only on Groq rate limits."""
    from backend.rag import GroqRequestError, ask_question

    for delay in (*GROQ_RETRY_DELAYS, None):
        try:
            # Follow-up suggestions aren't scored, so skip that extra Groq call.
            response = ask_question(
                question, images=images, suggest=False, max_words=ANSWER_MAX_WORDS
            )
        except GroqRequestError as exc:
            if delay is None or "rate limit" not in str(exc).lower():
                raise
            print(f"  Groq rate limit reached, retrying in {delay}s")
            time.sleep(delay)
            continue

        if not isinstance(response, dict):
            raise TypeError("ask_question() did not return a dictionary")
        return response
