
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
from transformers import pipeline


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CONTENT_MODERATION_MODEL = "openai/gpt-oss-safeguard-20b"
PROMPT_GUARD_MODEL = "Horizon-Labs/prompt-injection-guard-base"

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

    if not isinstance(question, str):
        raise ValueError("Question must be a string.")

    question = question.strip()

    if not question:
        raise ValueError("Question cannot be empty.")

    if len(question) > 2000:
        raise ValueError("Question cannot exceed 2000 characters.")

    return question


# --------------------------------------------------
# 2. Sanitization / normalization
# --------------------------------------------------

def sanitize_input(question: str) -> str:
    """Apply basic Unicode, HTML-tag, and whitespace normalization."""

    question = unicodedata.normalize("NFKC", question)
    question = re.sub(r"<[^>]*>", " ", question)
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

    return result["label"].upper() == "INJECTION"


# --------------------------------------------------
# 5. PII detection
# --------------------------------------------------

def detect_pii(question: str) -> bool:
    """Detect email addresses and common phone-number formats."""

    email_pattern = (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )
    phone_pattern = r"(?<!\w)(?:\+?\d[\d\s()-]{7,}\d)(?!\w)"

    return bool(
        re.search(email_pattern, question)
        or re.search(phone_pattern, question)
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
            max_completion_tokens=100,
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
    """Run input guardrails before processing a question."""

    # 1. Validate
    question = validate_question(question)

    # 2. Normalize
    question = sanitize_input(question)

    # 3. Prompt injection: fast regex check
    if detect_prompt_injection(question):
        raise ValueError("Potential prompt injection detected.")

    # 4. PII detection
    if detect_pii(question):
        raise ValueError(
            "Input contains potentially sensitive personal information."
        )

    # 5. Prohibited-content moderation
    if detect_prohibited_content(question):
        raise ValueError("Prohibited content detected.")

    # 6. Prompt injection: model-based check
    if detect_prompt_injection_model(question):
        raise ValueError("Potential prompt injection detected.")

    return question
