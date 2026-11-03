"""Score live text answers with RAGAS and apply the per-case quality floors."""

import asyncio
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

from evaluation.common import JUDGE_ATTEMPTS, judge_model, judge_model_args

METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
]

# Smoke runs on every PR, so it scores only the two strongest signals to save Groq tokens.
SUITE_METRICS = {
    "smoke": ["faithfulness", "answer_correctness"],
    "sanity": METRIC_NAMES,
}


def build_metrics(client, names):
    evaluator_llm = llm_factory(
        model=judge_model(),
        provider="openai",
        client=client,
        adapter="instructor",
        temperature=0,
        **judge_model_args(),
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
    return {name: metrics[name] for name in names}


async def score_metric(metric, kwargs):
    """Score one metric, retrying when the judge returns invalid JSON or NaN."""
    for attempt in range(1, JUDGE_ATTEMPTS + 1):
        try:
            value = float((await metric.ascore(**kwargs)).value)
            if math.isnan(value):
                raise ValueError("metric returned NaN")
            return value
        except Exception as exc:
            if attempt == JUDGE_ATTEMPTS:
                raise
            print(f"    attempt {attempt} failed ({type(exc).__name__}), retrying")
            await asyncio.sleep(5 * attempt)


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


async def score_text(records, case_min, metric_names):
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
        metrics = build_metrics(client, metric_names)

        for index, record in enumerate(to_score, start=1):
            print(f"\n[{index}/{len(to_score)}] Scoring {record['id']}")
            scores = {}
            errors = {}

            for name, metric in metrics.items():
                try:
                    value = await score_metric(metric, metric_kwargs(name, record))
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


def metric_averages(records, metric_names):
    averages = {}
    for name in metric_names:
        values = [r["scores"][name] for r in records if name in r.get("scores", {})]
        averages[name] = round(sum(values) / len(values), 3) if values else None
    return averages
