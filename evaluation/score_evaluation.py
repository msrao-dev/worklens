"""Score live text answers with RAGAS and apply the per-case quality floors."""

import math
import os

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

from evaluation.common import judge_model

METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
]


def build_metrics(client):
    evaluator_llm = llm_factory(
        model=judge_model(),
        provider="openai",
        client=client,
        adapter="instructor",
        temperature=0,
        # Groq's on-demand tier rejects any request asking for more than 1000 output tokens.
        max_tokens=900,
    )
    evaluator_embeddings = HuggingFaceEmbeddings(
        model="BAAI/bge-small-en-v1.5",
        device="cpu",
        normalize_embeddings=True,
    )
    return {
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


def metric_kwargs(name, record):
    """Supply only the arguments each metric accepts."""
    kwargs = {"user_input": record["question"]}
    if name in {"faithfulness", "context_precision", "context_recall"}:
        kwargs["retrieved_contexts"] = record["retrieved_contexts"]
    if name in {"faithfulness", "answer_relevancy", "answer_correctness"}:
        kwargs["response"] = record["answer"]
    if name in {"context_precision", "context_recall", "answer_correctness"}:
        kwargs["reference"] = record["ground_truth"]
    return kwargs


async def score_text(records, case_min):
    """Add RAGAS scores to answered records and set each record's final status."""
    to_score = [record for record in records if record["status"] == "answered"]
    if not to_score:
        return records

    client = AsyncOpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
        max_retries=5,
    )

    try:
        metrics = build_metrics(client)

        for index, record in enumerate(to_score, start=1):
            print(f"\n[{index}/{len(to_score)}] Scoring {record['id']}")
            scores = {}
            errors = {}

            for name, metric in metrics.items():
                try:
                    result = await metric.ascore(**metric_kwargs(name, record))
                    value = float(result.value)
                    if math.isnan(value):
                        raise ValueError("metric returned NaN")
                    scores[name] = round(value, 3)
                    print(f"  {name}: {value:.3f}")
                except Exception as exc:
                    errors[name] = f"{type(exc).__name__}: {exc}"
                    print(f"  {name} error: {exc}")

            record["scores"] = scores
            below = [
                f"{name} {scores[name]:.2f} < {floor}"
                for name, floor in case_min.items()
                if name in scores and scores[name] < floor
            ]

            if errors:
                record["status"] = "error"
                record["error"] = "; ".join(f"{name}: {msg}" for name, msg in errors.items())
            elif below:
                record["status"] = "fail"
                record["failure_reason"] = ", ".join(below)
            else:
                record["status"] = "passed"

    finally:
        await client.close()

    return records


def metric_averages(records):
    averages = {}
    for name in METRIC_NAMES:
        values = [r["scores"][name] for r in records if name in r.get("scores", {})]
        averages[name] = round(sum(values) / len(values), 3) if values else None
    return averages
