from typing import TypedDict

import fitz
from docx import Document


class IndexedChunk(TypedDict):
    text: str
    page_number: int | None
    line_start: int
    line_end: int


def extract_text_units(filepath: str) -> list[tuple[int | None, str]]:
    extension = filepath.rsplit(".", 1)[-1].lower()

    if extension == "pdf":
        with fitz.open(filepath) as pdf:
            return [
                (page_number, page.get_text("text"))
                for page_number, page in enumerate(pdf, start=1)
            ]

    if extension == "docx":
        doc = Document(filepath)
        return [(None, "\n".join(paragraph.text for paragraph in doc.paragraphs))]

    if extension == "txt":
        with open(filepath, encoding="utf-8") as file:
            return [(None, file.read())]

    raise ValueError("Unsupported file type")


def extract_text(filepath: str) -> str:
    """Return extracted text for callers that do not need page boundaries."""
    return "".join(text for _, text in extract_text_units(filepath))
