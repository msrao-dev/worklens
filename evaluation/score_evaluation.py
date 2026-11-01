
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from ragas.embeddings import HuggingFaceEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    AnswerCorrectness,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = PROJECT_ROOT / "evaluation" / "retrieval_results.json"

# Preserve the original baseline.
OUTPUT_PATH = PROJECT_ROOT / "evaluation" / "ragas_scores_v2.json"

# Start with one question. Change to False to evaluate all questions.
SMOKE_TEST = os.getenv("SMOKE_TEST", "false").lower() == "true"

load_dotenv(PROJECT_ROOT / ".env")
def get_reference(item):
    """Find the expected answer in the evaluation record."""
    for key in ("ground_truth", "reference", "expected_answer"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def main():
    with RESULTS_PATH.open("r", encoding="utf-8") as file:
        results = json.load(file)

    successful_results = [
        item
        for item in results
        if item.get("status") == "success"
        and item.get("answer")
        and item.get("retrieved_contexts")
    ]

    if not successful_results:
        raise RuntimeError(
            "No successful results with answers and retrieved contexts were found."
        )

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing from your .env file.")

    model_name = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )

    scored_results = []

    try:
        evaluator_llm = llm_factory(
            model=model_name,
            provider="openai",
            client=client,
            adapter="instructor",
            temperature=0,
            max_tokens=600,
        )

        evaluator_embeddings = HuggingFaceEmbeddings(
            model="BAAI/bge-small-en-v1.5",
            device="cpu",
            normalize_embeddings=True,
        )

        metrics = {
            "faithfulness": Faithfulness(llm=evaluator_llm),
            "answer_relevancy": AnswerRelevancy(
                llm=evaluator_llm,
                embeddings=evaluator_embeddings,
            ),
            "context_precision": ContextPrecision(llm=evaluator_llm),
            "context_recall": ContextRecall(llm=evaluator_llm),
            "answer_correctness": AnswerCorrectness(
                llm=evaluator_llm,
                embeddings=evaluator_embeddings,
            ),
        }

        SMOKE_TEST_IDS = {
            "EVAL001",
            "EVAL006",
            "EVAL009",
            "EVAL013",
            "EVAL014",
        }

        test_results = (
            [
                item for item in successful_results
                if item.get("id") in SMOKE_TEST_IDS
            ]
            if SMOKE_TEST
            else successful_results
        )

        total = len(test_results)

        for index, item in enumerate(test_results, start=1):
            question = item["question"]
            answer = item["answer"]
            contexts = item["retrieved_contexts"]
            reference = get_reference(item)

            item_id = item.get("id", "unknown")
            print(f"\n[{index}/{total}] Scoring {item_id}")

            record = {
                "id": item_id,
                "question": question,
                "answer": answer,
                "retrieved_contexts": contexts,
                "ground_truth": reference,
            }

            if not reference:
                record["reference_warning"] = (
                    "No reference answer found. "
                    "Context Precision, Context Recall, and "
                    "Answer Correctness will be skipped."
                )
                print("  Warning: reference answer not found.")

            for name, metric in metrics.items():

                # These metrics require a reference answer.
                if name in {
                    "context_precision",
                    "context_recall",
                    "answer_correctness",
                } and not reference:
                    print(f"  {name}: skipped (no reference)")
                    continue

                # Supply only the arguments each metric accepts.
                kwargs = {"user_input": question}

                if name in {
                    "faithfulness",
                    "context_precision",
                    "context_recall",
                }:
                    kwargs["retrieved_contexts"] = contexts

                if name in {
                    "faithfulness",
                    "answer_relevancy",
                    "answer_correctness",
                }:
                    kwargs["response"] = answer

                if name in {
                    "context_precision",
                    "context_recall",
                    "answer_correctness",
                }:
                    kwargs["reference"] = reference

                try:
                    result = await metric.ascore(**kwargs)
                    record[name] = float(result.value)
                    print(f"  {name}: {result.value:.3f}")

                except Exception as exc:
                    record[f"{name}_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                    print(f"  {name} error: {exc}")

            scored_results.append(record)

            # Save progress after every question.
            OUTPUT_PATH.write_text(
                json.dumps(
                    scored_results,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    finally:
        await client.close()

    metric_names = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "answer_correctness",
    ]

    print("\n--- RAGAS Evaluation Summary ---")
    print(f"Questions evaluated: {len(scored_results)}")

    for name in metric_names:
        scores = [
            record[name]
            for record in scored_results
            if isinstance(record.get(name), (int, float))
        ]

        print(f"\n{name}:")
        print(
            f"  Successful scores: "
            f"{len(scores)}/{len(scored_results)}"
        )

        if scores:
            average = sum(scores) / len(scores)
            print(f"  Average: {average:.3f}")
        else:
            print("  Average: unavailable")

    print(f"\nDetailed scores saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
