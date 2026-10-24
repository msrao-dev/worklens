import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakePage:
    def __init__(self, number, text):
        self.number = number
        self.text = text

    def get_text(self):
        return self.text


class FakePdf:
    def __init__(self):
        self.pages = [FakePage(0, "first page"), FakePage(1, "second page")]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.pages)


fake_fitz = types.ModuleType("fitz")
fake_fitz.open = lambda filepath: FakePdf()
fake_docx = types.ModuleType("docx")
fake_docx.Document = lambda filepath: types.SimpleNamespace(
    paragraphs=[types.SimpleNamespace(text="paragraph one"), types.SimpleNamespace(text="paragraph two")]
)
with patch.dict(sys.modules, {"fitz": fake_fitz, "docx": fake_docx}):
    ingest = importlib.import_module("backend.ingest")


class IngestTests(unittest.TestCase):
    def test_pdf_sections_keep_actual_page_numbers(self):
        sections = ingest.extract_sections("policy.pdf")

        self.assertEqual(
            sections,
            [
                ("first page", {"page_number": 1}),
                ("second page", {"page_number": 2}),
            ],
        )

    def test_docx_and_txt_do_not_invent_page_numbers(self):
        docx_sections = ingest.extract_sections("policy.docx")
        self.assertEqual(docx_sections, [("paragraph one\nparagraph two", {})])

        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / "notes.txt"
            text_path.write_text("plain text", encoding="utf-8")

            self.assertEqual(ingest.extract_sections(str(text_path)), [("plain text", {})])


if __name__ == "__main__":
    unittest.main()
