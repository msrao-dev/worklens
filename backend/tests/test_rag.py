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
        self.configuration = {"hnsw": {"space": "l2"}}
        self.query_calls = []
        self.query_result = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

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
        self.query_calls.append(kwargs)
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
        rag.MAX_CHROMA_DISTANCE = 0.5

    def test_relevant_result_returns_only_retrieved_citation_and_grounded_prompt(self):
        rag.collection.query_result = {
            "ids": [["stored-id-7", "too-distant"]],
            "documents": [[
                "Leave requests must be submitted in HRMS.",
                "Unrelated passage that exceeds the cutoff.",
            ]],
            "metadatas": [[
                {"source": "Leave Policy.pdf", "chunk_index": 7, "page_number": 3},
                {"source": "Other.pdf", "chunk_index": 2},
            ]],
            "distances": [[0.2, 0.8]],
        }
        with patch.object(rag, "generate_answer", return_value="Submit it in HRMS.") as generate:
            response = rag.ask_question("How do I request leave?")

        generate.assert_called_once()
        prompt = generate.call_args.args[0]
        self.assertIn("ONLY the evidence", prompt)
        self.assertIn("Do not use prior knowledge", prompt)
        self.assertIn("Leave requests must be submitted in HRMS.", prompt)
        self.assertNotIn("Unrelated passage", prompt)
        self.assertEqual(
            rag.collection.query_calls[0]["include"],
            ["documents", "metadatas", "distances"],
        )
        self.assertFalse(response.insufficient_evidence)
        self.assertEqual([citation.chunk_id for citation in response.citations], ["stored-id-7"])
        self.assertEqual(response.citations[0].chunk_id, "stored-id-7")
        self.assertEqual(response.citations[0].document_name, "Leave Policy.pdf")
        self.assertEqual(response.citations[0].chunk_index, 7)
        self.assertEqual(response.citations[0].page_number, 3)
        self.assertEqual(response.citations[0].text, "Leave requests must be submitted in HRMS.")

    def test_irrelevant_results_do_not_call_llm(self):
        rag.collection.query_result = {
            "ids": [["distant-id"]],
            "documents": [["Unrelated chunk."]],
            "metadatas": [[{"source": "Other.pdf"}]],
            "distances": [[0.9]],
        }
        with patch.object(rag, "generate_answer") as generate:
            response = rag.ask_question("Question unrelated to the collection")

        generate.assert_not_called()
        self.assertTrue(response.insufficient_evidence)
        self.assertEqual(response.citations, [])

    def test_empty_retrieval_does_not_call_llm(self):
        with patch.object(rag, "generate_answer") as generate:
            response = rag.ask_question("Question with no matches")

        generate.assert_not_called()
        self.assertTrue(response.insufficient_evidence)
        self.assertEqual(response.citations, [])
        self.assertIn("not found in the available knowledge base", response.answer)

    def test_unconfigured_cutoff_fails_closed(self):
        rag.MAX_CHROMA_DISTANCE = None
        rag.collection.query_result = {
            "ids": [["nearest-id"]],
            "documents": [["Potentially useful chunk."]],
            "metadatas": [[{"source": "Handbook.pdf"}]],
            "distances": [[0.01]],
        }
        with patch.object(rag, "generate_answer") as generate:
            response = rag.ask_question("Question")

        generate.assert_not_called()
        self.assertTrue(response.insufficient_evidence)
        self.assertIn("not calibrated", response.answer)

    def test_unknown_collection_metric_fails_closed(self):
        rag.collection.configuration = {"hnsw": {"space": "unknown"}}
        rag.collection.query_result = {
            "ids": [["nearest-id"]],
            "documents": [["Potentially useful chunk."]],
            "metadatas": [[{"source": "Handbook.pdf"}]],
            "distances": [[0.01]],
        }
        with patch.object(rag, "generate_answer") as generate:
            response = rag.ask_question("Question")

        generate.assert_not_called()
        self.assertTrue(response.insufficient_evidence)
        self.assertIn("distance metric is unavailable", response.answer)

    def test_malformed_or_missing_metadata_does_not_invent_details(self):
        rag.collection.query_result = {
            "ids": [["missing-meta", "bad-meta"]],
            "documents": [["Valid text without metadata.", "Valid text with malformed metadata."]],
            "metadatas": [[None, {"source": 4, "chunk_index": "2", "page_number": True}]],
            "distances": [[0.2, 0.3]],
        }
        with patch.object(rag, "generate_answer", return_value="Answer.") as generate:
            response = rag.ask_question("Question")

        generate.assert_called_once()
        self.assertEqual([citation.chunk_id for citation in response.citations], ["missing-meta", "bad-meta"])
        for citation in response.citations:
            self.assertIsNone(citation.document_name)
            self.assertIsNone(citation.chunk_index)
            self.assertIsNone(citation.page_number)

    def test_citations_are_scoped_to_current_query_and_require_valid_distances(self):
        rag.collection.query_result = {
            "ids": [["current-id", "missing-distance", "empty-text"]],
            "documents": [["Current result.", "No distance.", " \n "]],
            "metadatas": [[{"source": "Current.pdf"}, {"source": "Other.pdf"}, {"source": "Blank.pdf"}]],
            "distances": [[0.2]],
        }
        with patch.object(rag, "generate_answer", return_value="Current answer.") as generate:
            response = rag.ask_question("Current question")

        generate.assert_called_once()
        self.assertEqual([citation.chunk_id for citation in response.citations], ["current-id"])

        rag.collection.query_result = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        with patch.object(rag, "generate_answer") as generate:
            next_response = rag.ask_question("Different question")

        generate.assert_not_called()
        self.assertEqual(next_response.citations, [])
        self.assertTrue(next_response.insufficient_evidence)

    def test_repeated_ingestion_and_document_replacement_remove_obsolete_chunks(self):
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

        rag.store_chunks(
            ["Replacement document only has one chunk."],
            filename="policy.pdf",
            chunk_metadata=[{"page_number": 1}],
        )

        self.assertEqual(len(rag.collection.documents), 1)
        self.assertNotIn("Second chunk", [item["text"] for item in rag.collection.documents.values()])
        self.assertEqual(
            [item["metadata"]["page_number"] for item in rag.collection.documents.values()],
            [1],
        )
        self.assertEqual(rag.collection.delete_calls, [{"source": "policy.pdf"}] * 3)

    def test_non_finite_configured_distance_is_rejected(self):
        with patch.dict("os.environ", {"CHROMA_MAX_DISTANCE": "nan"}):
            with self.assertRaisesRegex(ValueError, "finite number"):
                rag._read_max_distance()


if __name__ == "__main__":
    unittest.main()
