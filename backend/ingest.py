import os
from typing import Any

import fitz
from docx import Document


def extract_sections(filepath: str) -> list[tuple[str, dict[str, Any]]]:
    extension = os.path.splitext(filepath)[1].lower()

    if extension == ".pdf":
        with fitz.open(filepath) as pdf:
            return [
                (page.get_text(), {"page_number": page.number + 1})
                for page in pdf
            ]

    if extension == ".docx":
        doc = Document(filepath)
        return [("\n".join(paragraph.text for paragraph in doc.paragraphs), {})]

    if extension == ".txt":
        with open(filepath, "r", encoding="utf-8") as file:
            return [(file.read(), {})]

    raise ValueError("Unsupported file type")


def extract_text(filepath: str) -> str:
    return "\n".join(text for text, _ in extract_sections(filepath))
