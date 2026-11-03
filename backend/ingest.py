import fitz
from docx import Document

def extract_text(filepath):

    if filepath.endswith(".pdf"):
        text = ""
        pdf = fitz.open(filepath)

        for page in pdf:
            text += page.get_text()

        return text

    elif filepath.endswith(".docx"):

        doc = Document(filepath)

        return "\n".join(
            [p.text for p in doc.paragraphs]
        )

    elif filepath.endswith(".txt"):

        with open(filepath,"r",encoding="utf-8") as f:
            return f.read()

    raise Exception("Unsupported file type")
