def validate_question(question: object) -> str:
    """Validate a user question before processing it."""

    if not isinstance(question, str):
        raise ValueError("Question must be a string.")

    question = question.strip()

    if not question:
        raise ValueError("Question cannot be empty.")

    if len(question) > 2000:
        raise ValueError("Question cannot exceed 2000 characters.")

    return question


import re

PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?(previous\s+)?instructions",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"show\s+(me\s+)?(your\s+)?hidden\s+instructions",
    r"pretend\s+you\s+have\s+no\s+restrictions",
]


def detect_prompt_injection(question: str) -> bool:
    """Detect common prompt injection phrases."""

    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, question, re.IGNORECASE):
            return True

    return False


def validate_input(question: object) -> str:
    """Run all input guardrails before processing a question."""

    question = validate_question(question)

    if detect_prompt_injection(question):
        raise ValueError("Potential prompt injection detected.")

    return question