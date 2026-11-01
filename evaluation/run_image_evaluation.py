
import json
import os
import mimetypes
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.rag import ask_question
EVALUATION_DIR = PROJECT_ROOT / "evaluation"
GOLDEN_FILE = EVALUATION_DIR / "golden_questions.json"
OUTPUT_FILE = EVALUATION_DIR / "image_evaluation_results.json"

PASS_THRESHOLD = 0.80

load_dotenv(PROJECT_ROOT / ".env")


def load_evaluator():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing from .env")

    return OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )


def evaluate_answer(client, question, reference, answer, contexts):
    """Use an LLM judge to score correctness, completeness and grounding."""

    prompt = f"""
Evaluate this image-based RAG answer against the reference answer.

Question:
{question}

Reference answer:
{reference}

Generated answer:
{answer}

Retrieved evidence:
{json.dumps(contexts, ensure_ascii=False)}

Score each criterion from 0.0 to 1.0:
- correctness: factual agreement with the reference
- completeness: coverage of important reference details
- grounding: support from the retrieved evidence

Rules:
- Do not reward a fluent answer if its facts are wrong.
- Penalize unsupported claims and important omissions.
- Treat equivalent wording as acceptable.
- If the evidence does not support a claim, do not assume it is true.
- Return JSON only, with this exact structure:
{{
  "correctness": 0.0,
  "completeness": 0.0,
  "grounding": 0.0,
  "reason": "Brief explanation"
}}
"""

    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
        messages=[
            {
                "role": "system",
                "content": "You are a strict RAG evaluation judge. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    scores = json.loads(content)

    for key in ("correctness", "completeness", "grounding"):
        value = scores.get(key)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"Invalid evaluator score for {key}: {value}")

    scores["overall_score"] = round(
        (
            scores["correctness"]
            + scores["completeness"]
            + scores["grounding"]
        ) / 3,
        3,
    )
    scores["passed"] = (
        scores["correctness"] >= PASS_THRESHOLD
        and scores["completeness"] >= PASS_THRESHOLD
        and scores["grounding"] >= PASS_THRESHOLD
    )

    return scores


def main():
    with GOLDEN_FILE.open("r", encoding="utf-8") as file:
        golden_questions = json.load(file)

    image_cases = [item for item in golden_questions if item.get("image")]

    if not image_cases:
        raise RuntimeError("No image questions found in golden_questions.json")

    # Validate references before making model calls.
    missing_references = [
        item.get("id", "unknown")
        for item in image_cases
        if not (
            isinstance(item.get("ground_truth"), str)
            and item["ground_truth"].strip()
        )
    ]

    if missing_references:
        raise RuntimeError(
            "Add a non-empty ground_truth reference for these image cases: "
            + ", ".join(missing_references)
        )

    client = load_evaluator()
    results = []

    try:
        for index, item in enumerate(image_cases, start=1):
            case_id = item["id"]
            question = item["question"]
            image_path = PROJECT_ROOT / item["image"]

            record = {
                "id": case_id,
                "question": question,
                "image": item["image"],
                "ground_truth": item["ground_truth"],
                "answer": "",
                "retrieved_contexts": [],
                "status": "error",
            }

            print(f"\n[{index}/{len(image_cases)}] {case_id}: {question}")

            try:
                if not question.strip():
                    raise ValueError("Question is empty")

                if not image_path.is_file():
                    raise FileNotFoundError(f"Image not found: {image_path}")

                mime_type, _ = mimetypes.guess_type(image_path.name)
                if mime_type not in {
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                }:
                    raise ValueError(f"Unsupported image type: {mime_type}")

                response = ask_question(
                    question.strip(),
                    images=[(image_path.read_bytes(), mime_type)],
                )

                if not isinstance(response, dict):
                    raise TypeError("ask_question() did not return a dictionary")

                answer = response.get("answer", "")
                contexts = response.get("sources", [])

                record["answer"] = answer
                record["retrieved_contexts"] = contexts

                if not isinstance(answer, str) or not answer.strip():
                    record["status"] = "fail"
                    record["failure_reason"] = "Empty answer"

                elif not contexts:
                    record["status"] = "fail"
                    record["failure_reason"] = "No retrieved evidence"

                else:
                    scores = evaluate_answer(
                        client=client,
                        question=question,
                        reference=item["ground_truth"],
                        answer=answer,
                        contexts=contexts,
                    )
                    record["scores"] = scores
                    record["status"] = "passed" if scores["passed"] else "fail"

                print(f"Status: {record['status']}")
                if "scores" in record:
                    print(json.dumps(record["scores"], indent=2))
                elif "failure_reason" in record:
                    print(record["failure_reason"])

            except Exception as exc:
                record["status"] = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"
                print(f"ERROR: {record['error']}")

            results.append(record)

            # Save progress after every case, even if a later case fails.
            OUTPUT_FILE.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    finally:
        client.close()

    passed = sum(item["status"] == "passed" for item in results)
    failed = sum(item["status"] == "fail" for item in results)
    errors = sum(item["status"] == "error" for item in results)

    print(f"\nImage evaluation summary: {passed} passed, {failed} failed, {errors} errors")
    print(f"Results saved to: {OUTPUT_FILE}")

    # Make CI fail when quality checks fail or cannot be completed.
    if failed or errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
