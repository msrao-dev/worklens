import importlib
import sys
import types
import unittest
from unittest.mock import patch


class Encoded:
    def tolist(self):
        return [0.1, 0.2]


class EncodedBatch(list):
    def tolist(self):
        return [item.tolist() for item in self]


class FakeModel:
    def encode(self, value):
        if isinstance(value, list):
            return EncodedBatch(Encoded() for _ in value)
        return Encoded()


class FakeCollection:
    def __init__(self):
        self.documents = {}
        self.delete_calls = []
        self.query_result = {"ids": [[]], "documents": [[]], "metadatas": [[]]}

    def delete(self, where):
        self.delete_calls.append(where)
        source = where["source"]
        self.documents = {
            chunk_id: item
            for chunk_id, item in self.documents.items()
            if item["metadata"]["source"] != source
        }

    def upsert(self, ids, documents, embeddings, metadatas):
        for chunk_id, text, metadata in zip(ids, documents, metadatas):
            self.documents[chunk_id] = {"text": text, "metadata": metadata}

    def query(self, **kwargs):
        return self.query_result


fake_chromadb = types.ModuleType("chromadb")
fake_chromadb.PersistentClient = lambda path: types.SimpleNamespace(
    get_or_create_collection=lambda name: FakeCollection()
)
fake_transformers = types.ModuleType("sentence_transformers")
fake_transformers.SentenceTransformer = lambda model_name: FakeModel()
fake_dotenv = types.ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda: None
with patch.dict(
    sys.modules,
    {
        "chromadb": fake_chromadb,
        "sentence_transformers": fake_transformers,
        "dotenv": fake_dotenv,
    },
):
    rag = importlib.import_module("backend.rag")


class RagTests(unittest.TestCase):
    def setUp(self):
        rag.MODEL = FakeModel()
        rag.collection = FakeCollection()

    def test_returns_citations_from_retrieved_chunks(self):
        rag.collection.query_result = {
            "ids": [["stored-id-7"]],
            "documents": [["Leave requests must be submitted in HRMS."]],
            "metadatas": [[
                {
                    "source": "Leave Policy.pdf",
                    "chunk_index": 7,
                    "page_number": 3,
                }
            ]],
        }
        with patch.object(rag, "generate_answer", return_value="Submit it in HRMS.") as generate:
            response = rag.ask_question("How do I request leave?")

        generate.assert_called_once()
        self.assertFalse(response.insufficient_evidence)
        self.assertEqual(response.citations[0].chunk_id, "stored-id-7")
        self.assertEqual(response.citations[0].document_name, "Leave Policy.pdf")
        self.assertEqual(response.citations[0].chunk_index, 7)
        self.assertEqual(response.citations[0].page_number, 3)
        self.assertEqual(response.citations[0].text, "Leave requests must be submitted in HRMS.")

    def test_empty_retrieval_does_not_call_llm(self):
        with patch.object(rag, "generate_answer") as generate:
            response = rag.ask_question("Question with no matches")

        generate.assert_not_called()
        self.assertTrue(response.insufficient_evidence)
        self.assertEqual(response.citations, [])
        self.assertIn("not found in the available knowledge base", response.answer)

    def test_blank_retrieved_text_is_insufficient_evidence(self):
        rag.collection.query_result = {
            "ids": [["blank-chunk"]],
            "documents": [[" \n "]],
            "metadatas": [[{"source": "empty.txt", "chunk_index": 0}]],
        }
        with patch.object(rag, "generate_answer") as generate:
            response = rag.ask_question("Question")

        generate.assert_not_called()
        self.assertTrue(response.insufficient_evidence)
        self.assertEqual(response.citations, [])

    def test_reingestion_replaces_chunks_without_duplicates(self):
        chunk_metadata = [{"page_number": 1}, {"page_number": 2}]
        rag.store_chunks(
            ["First chunk", "Second chunk"],
            filename="policy.pdf",
            chunk_metadata=chunk_metadata,
        )
        first_ids = set(rag.collection.documents)

        rag.store_chunks(
            ["First chunk", "Second chunk"],
            filename="policy.pdf",
            chunk_metadata=chunk_metadata,
        )

        self.assertEqual(set(rag.collection.documents), first_ids)
        self.assertEqual(len(rag.collection.documents), 2)
        self.assertEqual(
            [item["metadata"]["page_number"] for item in rag.collection.documents.values()],
            [1, 2],
        )
        self.assertEqual(rag.collection.delete_calls, [{"source": "policy.pdf"}] * 2)


if __name__ == "__main__":
    unittest.main()
