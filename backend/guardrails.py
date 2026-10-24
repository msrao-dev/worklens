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