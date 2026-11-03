
import json
import os
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from groq.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CONTENT_MODERATION_MODEL = "openai/gpt-oss-safeguard-20b"
PROMPT_GUARD_MODEL = "Horizon-Labs/prompt-injection-guard-base"
# Real attacks score 0.98+; formatting requests like "restrict your response
# to 200 words" come back as INJECTION at ~0.5, so ignore low-confidence hits.
PROMPT_GUARD_THRESHOLD = 0.90

MAX_QUESTION_LENGTH = 2000
MAX_HISTORY_TURNS = 4  # rag.py only sends the last 4 turns to the LLM

_prompt_guard_classifier = None


CONTENT_MODERATION_POLICY = """
Classify whether the user's request violates this content policy.

Mark violation=true for requests that:
- Threaten, target, or encourage violence against a person.
- Seek actionable instructions to harm or abuse someone.
- Promote hateful abuse or targeted harassment.
- Request instructions facilitating serious wrongdoing.

Mark violation=false for:
- Normal workplace and employee-policy questions.
- Neutral discussion, reporting, or prevention of harmful behavior.
- Educational or safety-related questions that do not facilitate harm.

Return only a JSON object:
{"violation": true}
or
{"violation": false}
"""


class InputValidationError(ValueError):
    """The question is missing or malformed (HTTP 422)."""


class GuardrailViolation(ValueError):
    """The question was blocked by a guardrail (HTTP 400)."""


PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?(previous\s+)?instructions",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"show\s+(me\s+)?(your\s+)?hidden\s+instructions",
    r"pretend\s+you\s+have\s+no\s+restrictions",
]


# --------------------------------------------------
# 1. Input validation
# --------------------------------------------------

def validate_question(question: object) -> str:
    """Validate the type, emptiness, and length of a question."""

    if not isinstance(question, str) or not question.strip():
        raise InputValidationError("A non-empty question is required.")

    question = question.strip()

    if len(question) > MAX_QUESTION_LENGTH:
        raise InputValidationError(
            f"Question cannot exceed {MAX_QUESTION_LENGTH} characters."
        )

    return question


# --------------------------------------------------
# 2. Sanitization / normalization
# --------------------------------------------------

def sanitize_input(question: str) -> str:
    """Apply basic Unicode, HTML-tag, and whitespace normalization."""

    question = unicodedata.normalize("NFKC", question)
    # Only strip real tags, so "salary < 5000 and > 1000" stays intact.
    question = re.sub(r"</?[A-Za-z][^>]*>", " ", question)
    question = re.sub(r"\s+", " ", question).strip()

    return question


# --------------------------------------------------
# 3. Prompt-injection detection: regex
# --------------------------------------------------

def detect_prompt_injection(question: str) -> bool:
    """Detect known prompt-injection phrases using regex."""

    return any(
        re.search(pattern, question, re.IGNORECASE)
        for pattern in PROMPT_INJECTION_PATTERNS
    )


# --------------------------------------------------
# 4. Prompt-injection detection: local model
# --------------------------------------------------

def detect_prompt_injection_model(question: str) -> bool:
    """Classify potential prompt injection using a local model."""

    global _prompt_guard_classifier

    if _prompt_guard_classifier is None:
        # Imported lazily: transformers/torch are slow to load and not
        # needed by code (or tests) that never reach this check.
        from transformers import pipeline

        _prompt_guard_classifier = pipeline(
            "text-classification",
            model=PROMPT_GUARD_MODEL,
            device=-1,
        )

    result = _prompt_guard_classifier(
        question,
        truncation=True,
        max_length=512,
    )[0]

    return (
        result["label"].upper() == "INJECTION"
        and result["score"] >= PROMPT_GUARD_THRESHOLD
    )


# --------------------------------------------------
# 5. PII detection
# --------------------------------------------------

def detect_pii(question: str) -> bool:
    """Detect email addresses and common phone-number formats."""

    email_pattern = (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )
    phone_candidate = r"(?<!\w)\+?\d[\d\s()-]{7,}\d(?!\w)"

    if re.search(email_pattern, question):
        return True

    # Phone numbers have 10-15 digits; this skips years like "2023-2024"
    # and dates like "01-01-2025".
    return any(
        10 <= len(re.sub(r"\D", "", match)) <= 15
        for match in re.findall(phone_candidate, question)
    )


# --------------------------------------------------
# 6. Prohibited-content detection: Groq model
# --------------------------------------------------

def detect_prohibited_content(question: str) -> bool:
    """Return True if the moderation model flags prohibited content."""

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    try:
        client = Groq(api_key=api_key)

        messages: list[
            ChatCompletionSystemMessageParam
            | ChatCompletionUserMessageParam
        ] = [
            {
                "role": "system",
                "content": CONTENT_MODERATION_POLICY,
            },
            {
                "role": "user",
                "content": question,
            },
        ]

        response = client.chat.completions.create(
            model=CONTENT_MODERATION_MODEL,
            messages=messages,
            temperature=0,
            # gpt-oss is a reasoning model: reasoning tokens count toward
            # this limit, so 100 left the JSON answer empty or truncated.
            max_completion_tokens=1024,
            reasoning_effort="low",
            include_reasoning=False,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content

        if not content:
            raise ValueError("Empty moderation response.")

        result = json.loads(content)
        violation = result.get("violation")

        if not isinstance(violation, bool):
            raise ValueError("Invalid moderation response format.")

        return violation

    except Exception as exc:
        raise RuntimeError(
            "Content moderation failed; request cannot be evaluated."
        ) from exc


# --------------------------------------------------
# Main input-guardrail pipeline
# --------------------------------------------------

def validate_input(question: object) -> str:
    """Run input guardrails before processing a question.

    Checks run cheapest first, so the paid Groq call only happens for input
    that passed every local check.
    """

    # 1. Validate
    question = validate_question(question)

    # 2. Normalize
    question = sanitize_input(question)

    # 3. Prompt injection: fast regex check
    if detect_prompt_injection(question):
        raise GuardrailViolation("Potential prompt injection detected.")

    # 4. PII detection
    if detect_pii(question):
        raise GuardrailViolation(
            "Input contains potentially sensitive personal information."
        )

    # 5. Prompt injection: local model check
    if detect_prompt_injection_model(question):
        raise GuardrailViolation("Potential prompt injection detected.")

    # 6. Prohibited-content moderation (network call)
    if detect_prohibited_content(question):
        raise GuardrailViolation("Prohibited content detected.")

    return question


def validate_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop history turns that look like prompt injection.

    History comes from the client, so it can be forged; without this check
    an injection placed in "history" skips every guardrail. Flagged turns are
    dropped rather than rejected, because the frontend also sends earlier
    questions that were already blocked, which would block every follow-up.
    """

    recent = history[-MAX_HISTORY_TURNS:]

    return [
        turn
        for turn in recent
        if not _looks_like_injection(
            sanitize_input(str(turn.get("text", "")))[:MAX_QUESTION_LENGTH]
        )
    ]


def _looks_like_injection(text: str) -> bool:
    return bool(text) and (
        detect_prompt_injection(text) or detect_prompt_injection_model(text)
    )
