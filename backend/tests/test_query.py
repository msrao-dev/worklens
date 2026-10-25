import base64
import importlib
import io
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image


class _Vector(list):
    def tolist(self):
        return list(self)


class _EmbeddingModel:
    def __init__(self, _model_name):
        pass

    def encode(self, _text):
        return _Vector([0.1, 0.2])


class _Collection:
    def query(self, **_kwargs):
        return {
            "documents": [["Approved leave policy: 20 days per year."]],
            "metadatas": [[{"source": "Employee Handbook.pdf", "chunk_index": 4}]],
        }


class _ChromaClient:
    def get_or_create_collection(self, _name):
        return _Collection()

    def delete_collection(self, _name):
        pass


fake_sentence_transformers = types.ModuleType("sentence_transformers")
fake_sentence_transformers.SentenceTransformer = _EmbeddingModel
fake_chromadb = types.ModuleType("chromadb")
fake_chromadb.PersistentClient = lambda **_kwargs: _ChromaClient()
fake_ingest = types.ModuleType("backend.ingest")
fake_ingest.extract_text = lambda _filepath: ""
fake_text_splitters = types.ModuleType("langchain_text_splitters")
fake_text_splitters.RecursiveCharacterTextSplitter = object
with patch.dict(
    sys.modules,
    {
        "sentence_transformers": fake_sentence_transformers,
        "chromadb": fake_chromadb,
        "backend.ingest": fake_ingest,
        "langchain_text_splitters": fake_text_splitters,
    },
):
    api = importlib.import_module("backend.app")
    rag = importlib.import_module("backend.rag")

client = TestClient(api.app)


def _png_bytes():
    image = Image.new("RGB", (2, 2), color="blue")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@contextmanager
def _groq_mock(failure=None):
    requests = []

    class FakeAPIError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    def create(**kwargs):
        requests.append(kwargs)
        if failure is not None:
            raise FakeAPIError(failure)
        return types.SimpleNamespace(
            model="qwen/qwen3.8-27b",
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="The image shows a leave balance.")
                )
            ],
        )

    class FakeGroq:
        def __init__(self, api_key):
            assert api_key == "unit-test-key"
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create)
            )

    groq_module = types.ModuleType("groq")
    groq_module.APIError = FakeAPIError
    groq_module.Groq = FakeGroq
    with patch.dict(sys.modules, {"groq": groq_module}):
        with patch.object(rag, "LLM_PROVIDER", "groq"), \
                patch.object(rag, "GROQ_API_KEY", "unit-test-key"):
            yield requests


class QueryEndpointTests(unittest.TestCase):
    def test_text_only_json_query_preserves_existing_response_shape(self):
        with patch.object(
            api,
            "ask_question",
            return_value={"answer": "Twenty days.", "sources": ["Policy passage"]},
        ) as ask:
            response = client.post("/query", json={"question": "How many leave days?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"answer": "Twenty days.", "sources": ["Policy passage"]},
        )
        ask.assert_called_once_with("How many leave days?")

    def test_text_only_query_uses_existing_rag_context_and_groq_text_format(self):
        with _groq_mock() as requests, \
                patch.object(rag, "GROQ_MODEL_NAME", "qwen/qwen3.8-27b"):
            response = client.post("/query", json={"question": "How many leave days?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sources"], ["Approved leave policy: 20 days per year."])
        self.assertEqual(
            response.json()["source_refs"],
            [{
                "source": "Employee Handbook.pdf",
                "chunk_index": 4,
                "text": "Approved leave policy: 20 days per year.",
            }],
        )
        message_content = requests[0]["messages"][0]["content"]
        self.assertIsInstance(message_content, str)
        self.assertIn("Approved leave policy: 20 days per year.", message_content)

    def test_valid_image_query_sends_image_and_retrieved_context_to_groq(self):
        image_bytes = _png_bytes()
        with _groq_mock() as requests, \
                patch.object(rag, "GROQ_MODEL_NAME", "qwen/qwen3.8-27b"):
            response = client.post(
                "/query",
                data={"question": "What leave balance appears in this screenshot?"},
                files=[("image", ("balance.png", image_bytes, "image/png"))],
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "The image shows a leave balance.")
        self.assertEqual(response.json()["sources"], ["Approved leave policy: 20 days per year."])

        content = requests[0]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("Approved leave policy: 20 days per year.", content[0]["text"])
        self.assertIn("not evidence of company policy", content[0]["text"])
        image_url = content[1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(image_url.split(",", 1)[1]), image_bytes)

    def test_rejects_unsupported_image_mime_type(self):
        response = client.post(
            "/query",
            data={"question": "What is shown?"},
            files=[("image", ("picture.gif", b"GIF89a", "image/gif"))],
        )

        self.assertEqual(response.status_code, 415)
        self.assertIn("JPEG, PNG, or WebP", response.json()["detail"])

    def test_rejects_image_content_that_does_not_match_mime_type(self):
        image = Image.new("RGB", (2, 2), color="red")
        output = io.BytesIO()
        image.save(output, format="JPEG")
        response = client.post(
            "/query",
            data={"question": "What is shown?"},
            files=[("image", ("picture.png", output.getvalue(), "image/png"))],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("does not match", response.json()["detail"])

    def test_rejects_malformed_image_content(self):
        response = client.post(
            "/query",
            data={"question": "What is shown?"},
            files=[("image", ("picture.png", b"not a PNG", "image/png"))],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("malformed", response.json()["detail"])

    def test_rejects_oversized_images(self):
        response = client.post(
            "/query",
            data={"question": "What is shown?"},
            files=[("image", ("large.png", b"x" * (api.MAX_IMAGE_BYTES + 1), "image/png"))],
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("10 MB", response.json()["detail"])

    def test_reports_malformed_and_empty_queries(self):
        malformed = client.post(
            "/query",
            content="{not-json",
            headers={"content-type": "application/json"},
        )
        empty = client.post("/query", json={"question": "  "})

        self.assertEqual(malformed.status_code, 400)
        self.assertIn("malformed", malformed.json()["detail"])
        self.assertEqual(empty.status_code, 422)
        self.assertIn("non-empty question", empty.json()["detail"])

    def test_rejects_vision_query_when_configured_groq_model_is_not_supported(self):
        with _groq_mock() as requests, \
                patch.object(rag, "GROQ_MODEL_NAME", "text-only-model"):
            response = client.post(
                "/query",
                data={"question": "What is shown?"},
                files=[("image", ("picture.png", _png_bytes(), "image/png"))],
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("does not support image input", response.json()["detail"])
        self.assertEqual(requests, [])

    def test_rejects_image_query_when_groq_is_not_the_active_provider(self):
        with patch.object(api, "ask_question", rag.ask_question), \
                patch.object(rag, "LLM_PROVIDER", "ollama"):
            response = client.post(
                "/query",
                data={"question": "What is shown?"},
                files=[("image", ("picture.png", _png_bytes(), "image/png"))],
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("LLM_PROVIDER=groq", response.json()["detail"])

    def test_reports_groq_api_failures_without_returning_a_fallback_answer(self):
        with _groq_mock(failure=429) as _requests, \
                patch.object(rag, "GROQ_MODEL_NAME", "qwen/qwen3.8-27b"):
            response = client.post("/query", json={"question": "How many leave days?"})

        self.assertEqual(response.status_code, 502)
        self.assertIn("rate limit", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
