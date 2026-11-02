import base64
import importlib
import io
import json
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
            "metadatas": [[{
                "source": "Employee Handbook.pdf",
                "chunk_index": 4,
                "page_number": 2,
                "line_start": 3,
                "line_end": 4,
            }]],
            "distances": [[0.12]],
        }

    def get_or_create_collection(self, _name):
        return self

    def delete_collection(self, _name):
        pass


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
fake_ingest.extract_text_units = lambda _filepath: []
fake_ingest.IndexedChunk = dict
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
def _groq_mock(failure=None, custom_content="The image shows a leave balance."):
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
                    message=types.SimpleNamespace(content=custom_content)
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
    def test_leave_encashment_answer_uses_retrieved_handbook_context(self):
        relevant_chunk = (
            "Accumulated unused leave may be encashed at the employee's last drawn "
            "monthly basic salary."
        )
        collection = types.SimpleNamespace(query=lambda **_kwargs: {
            "documents": [[relevant_chunk]],
            "metadatas": [[{"source": "Employee Handbook.pdf", "chunk_index": 18}]],
            "distances": [[0.31]],
        })
        with patch.object(rag, "collection", collection), \
                patch.object(
                    rag,
                    "generate_answer",
                    return_value=(
                        "Accumulated unused leave is encashed based on the last drawn "
                        "monthly basic salary."
                    ),
                ) as generate, \
                patch.object(rag, "generate_suggested_questions", return_value=[]):
            response = client.post("/query", json={"question": "How does leave encashment work?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["answer"],
            "Accumulated unused leave is encashed based on the last drawn monthly basic salary.",
        )
        self.assertIn(relevant_chunk, generate.call_args.args[0])
        self.assertIn("If any passage supports an answer", generate.call_args.args[0])

    def test_unrelated_query_with_no_retrieved_context_returns_explicit_fallback(self):
        collection = types.SimpleNamespace(query=lambda **_kwargs: {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        })
        with patch.object(rag, "collection", collection), \
                patch.object(rag, "generate_answer", return_value="Information not found.") as generate, \
                patch.object(rag, "generate_suggested_questions", return_value=[]):
            response = client.post("/query", json={"question": "What is the capital of France?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Information not found.")
        self.assertEqual(response.json()["sources"], [])
        self.assertIn("Only if none of the passages contains information relevant", generate.call_args.args[0])

    def test_relevant_chunk_ranked_below_unrelated_chunks_is_included_in_context(self):
        relevant_chunk = (
            "Accumulated leave is encashed on the last drawn monthly basic salary."
        )
        documents = [
            "General attendance guidelines.",
            "Office opening hours and access information.",
            "Guidance for submitting expense claims.",
            "Instructions for requesting equipment.",
            relevant_chunk,
        ]
        distances = [[0.04, 0.08, 0.11, 0.16, 0.22]]
        collection = types.SimpleNamespace(query=lambda **_kwargs: {
            "documents": [documents],
            "metadatas": [[{"source": "Handbook.pdf", "chunk_index": i} for i in range(5)]],
            "distances": distances,
        })
        with patch.object(rag, "collection", collection), \
                patch.object(rag, "generate_answer", return_value="It uses the last drawn monthly basic salary.") as generate, \
                patch.object(rag, "generate_suggested_questions", return_value=[]), \
                self.assertLogs(rag.logger, level="INFO") as captured:
            result = rag.ask_question("How does leave encashment work?")

        prompt = generate.call_args.args[0]
        self.assertIn(relevant_chunk, prompt)
        self.assertIn("Retrieved passage 5:", prompt)
        self.assertEqual(result["sources"], documents)
        self.assertTrue(any("rank=5 distance=0.22" in line for line in captured.output))
        self.assertFalse(any(relevant_chunk in line for line in captured.output))

    def test_valid_answer_containing_fallback_phrase_is_returned_unchanged(self):
        answer = (
            "The handbook says unused leave is encashed at the last drawn monthly basic "
            "salary. “Information not found” applies only when no relevant passage exists."
        )
        with patch.object(rag, "generate_answer", return_value=answer), \
                patch.object(rag, "generate_suggested_questions", return_value=[]):
            response = client.post("/query", json={"question": "How does leave encashment work?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], answer)

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
            {
                "answer": "Twenty days.",
                "sources": ["Policy passage"],
                "suggested_questions": [],
            },
        )
        ask.assert_called_once_with("How many leave days?", None, history=[])

    def test_greeting_routes_directly_to_llm_and_returns_nudge(self):
        with _groq_mock(custom_content="Hello! How can I assist you with the handbook today?") as requests, \
                patch.object(rag, "GROQ_MODEL_NAME", "qwen/qwen3.8-27b"):
            response = client.post("/query", json={"question": "Hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["answer"],
            "Hello! How can I assist you with the handbook today?",
        )
        self.assertEqual(response.json()["sources"], [])
        self.assertEqual(response.json()["source_refs"], [])
        self.assertTrue(response.json()["is_greeting"])
        self.assertIn("employee handbook", requests[0]["messages"][0]["content"])

    def test_query_returns_context_grounded_suggested_questions(self):
        suggestions = [
            "How many leave days are provided each year?",
            "What is the annual leave allowance?",
            "Does the policy state an allowance of 20 days?",
        ]
        with patch.object(
            rag,
            "generate_answer",
            side_effect=["Twenty days per year.", json.dumps(suggestions)],
        ) as generate:
            response = client.post("/query", json={"question": "How many leave days?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Twenty days per year.")
        self.assertEqual(response.json()["sources"], ["Approved leave policy: 20 days per year."])
        self.assertEqual(response.json()["suggested_questions"], suggestions)
        self.assertEqual(generate.call_count, 2)
        suggestion_prompt = generate.call_args.args[0]
        self.assertIn("How many leave days?", suggestion_prompt)
        self.assertIn("Twenty days per year.", suggestion_prompt)
        self.assertIn("Approved leave policy: 20 days per year.", suggestion_prompt)
        self.assertIn("only information explicitly present", suggestion_prompt)

    def test_suggestion_generation_failure_preserves_answer_and_sources(self):
        with patch.object(
            rag,
            "generate_answer",
            side_effect=["Twenty days per year.", RuntimeError("provider unavailable")],
        ):
            result = rag.ask_question("How many leave days?")

        self.assertEqual(result["answer"], "Twenty days per year.")
        self.assertEqual(result["sources"], ["Approved leave policy: 20 days per year."])
        self.assertEqual(result["suggested_questions"], [])

    def test_invalid_suggestion_output_returns_empty_array(self):
        with patch.object(
            rag,
            "generate_answer",
            side_effect=["Twenty days per year.", '["Only one question?"]'],
        ):
            result = rag.ask_question("How many leave days?")

        self.assertEqual(result["answer"], "Twenty days per year.")
        self.assertEqual(result["suggested_questions"], [])

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
                "page_number": 2,
                "line_start": 3,
                "line_end": 4,
                "text": "Approved leave policy: 20 days per year.",
            }],
        )
        message_content = requests[0]["messages"][0]["content"]
        self.assertIsInstance(message_content, str)
        self.assertIn("Approved leave policy: 20 days per year.", message_content)

    def test_indexed_chunks_keep_page_and_extracted_line_ranges(self):
        class FakeSplitter:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def create_documents(self, texts):
                text = texts[0]
                chunks = [
                    ("First policy line.", 0),
                    ("Second policy line.", 19),
                ]
                return [
                    types.SimpleNamespace(
                        page_content=chunk,
                        metadata={"start_index": start},
                    )
                    for chunk, start in chunks
                    if text[start:start + len(chunk)] == chunk
                ]

        with patch.object(api, "RecursiveCharacterTextSplitter", FakeSplitter):
            chunks = api._split_text_units([
                (3, "First policy line.\nSecond policy line."),
            ])

        self.assertEqual(
            chunks,
            [
                {
                    "text": "First policy line.",
                    "page_number": 3,
                    "line_start": 1,
                    "line_end": 1,
                },
                {
                    "text": "Second policy line.",
                    "page_number": 3,
                    "line_start": 2,
                    "line_end": 2,
                },
            ],
        )

    def test_store_chunks_persists_citation_locations_as_chroma_metadata(self):
        stored = {}
        collection = types.SimpleNamespace(add=lambda **kwargs: stored.update(kwargs))
        chunks = [{
            "text": "Policy excerpt.",
            "page_number": 2,
            "line_start": 5,
            "line_end": 6,
        }]

        with patch.object(rag, "MODEL", types.SimpleNamespace(
            encode=lambda documents: _Vector([[0.1, 0.2] for _ in documents])
        )), patch.object(rag, "collection", collection):
            rag.store_chunks(chunks, filename="Handbook.pdf")

        self.assertEqual(stored["documents"], ["Policy excerpt."])
        self.assertEqual(
            stored["metadatas"],
            [{
                "source": "Handbook.pdf",
                "chunk_index": 0,
                "page_number": 2,
                "line_start": 5,
                "line_end": 6,
            }],
        )

    def test_retrieved_sources_include_indexed_page_and_line_numbers(self):
        with patch.object(rag, "generate_answer", return_value="Twenty days per year."), \
                patch.object(rag, "generate_suggested_questions", return_value=[]):
            result = rag.ask_question("How many leave days?")

        self.assertEqual(
            result["source_refs"][0],
            {
                "source": "Employee Handbook.pdf",
                "chunk_index": 4,
                "page_number": 2,
                "line_start": 3,
                "line_end": 4,
                "text": "Approved leave policy: 20 days per year.",
            },
        )

    def test_query_with_history_passes_turns_and_contextualizes(self):
        history = [
            {"role": "user", "text": "tell me calculation for encashment for earned leaves"},
            {"role": "assistant", "text": "Encashment is calculated on basic pay."},
        ]
        with _groq_mock(custom_content="Here is the TLDR on leave encashment.") as requests, \
                patch.object(rag, "GROQ_MODEL_NAME", "qwen/qwen3.8-27b"):
            response = client.post(
                "/query",
                json={"question": "give me TLDR", "history": history},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Here is the TLDR on leave encashment.")
        # At least one request was made to contextualize or generate with history
        final_prompt = requests[-1]["messages"][0]["content"]
        self.assertIn("Recent Conversation History:", final_prompt)
        self.assertIn("tell me calculation for encashment for earned leaves", final_prompt)

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
