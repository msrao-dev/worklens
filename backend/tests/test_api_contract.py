import importlib
import sys
import types
import unittest
from unittest.mock import patch


class FakeModel:
    def encode(self, value):
        class Embedding(list):
            def tolist(self):
                return [0.1, 0.2]

        if isinstance(value, list):
            return [Embedding([0.1, 0.2]) for _ in value]
        return Embedding([0.1, 0.2])


class FakeCollection:
    configuration = {"hnsw": {"space": "l2"}}
    metadata = None

    def delete(self, **kwargs):
        pass

    def query(self, **kwargs):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def upsert(self, **kwargs):
        pass


fake_chromadb = types.ModuleType("chromadb")
fake_chromadb.PersistentClient = lambda path: types.SimpleNamespace(
    get_or_create_collection=lambda name: FakeCollection()
)
fake_transformers = types.ModuleType("sentence_transformers")
fake_transformers.SentenceTransformer = lambda model_name: FakeModel()
fake_dotenv = types.ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda: None
fake_fitz = types.ModuleType("fitz")
fake_docx = types.ModuleType("docx")
fake_docx.Document = lambda filepath: types.SimpleNamespace(paragraphs=[])
fake_langchain = types.ModuleType("langchain_text_splitters")
fake_langchain.RecursiveCharacterTextSplitter = object

with patch.dict(
    sys.modules,
    {
        "chromadb": fake_chromadb,
        "sentence_transformers": fake_transformers,
        "dotenv": fake_dotenv,
        "fitz": fake_fitz,
        "docx": fake_docx,
        "langchain_text_splitters": fake_langchain,
    },
):
    api = importlib.import_module("backend.app")
    rag = importlib.import_module("backend.rag")


class ApiContractTests(unittest.TestCase):
    def test_query_http_response_matches_frontend_contract(self):
        from fastapi.testclient import TestClient

        answer = rag.AnswerResponse(
            answer="Submit leave requests in HRMS.",
            citations=[
                rag.Citation(
                    chunk_id="retrieved-id",
                    document_name="Employee Handbook September 2026.pdf",
                    chunk_index=4,
                    page_number=2,
                    text="Submit leave requests in HRMS.",
                )
            ],
            insufficient_evidence=False,
        )
        with patch.object(api, "ask_question", return_value=answer):
            response = TestClient(api.app).post("/query", json={"question": "How do I request leave?"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {"answer", "citations", "insufficient_evidence"},
        )
        self.assertEqual(payload["answer"], "Submit leave requests in HRMS.")
        self.assertFalse(payload["insufficient_evidence"])
        self.assertEqual(
            payload["citations"],
            [
                {
                    "chunk_id": "retrieved-id",
                    "document_name": "Employee Handbook September 2026.pdf",
                    "chunk_index": 4,
                    "page_number": 2,
                    "text": "Submit leave requests in HRMS.",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
