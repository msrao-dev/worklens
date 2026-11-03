"""Run the image golden cases through the RAG pipeline and score them with an LLM judge."""

import json
import mimetypes
import os

from openai import OpenAI

from evaluation.common import PROJECT_ROOT, ask_with_retry, judge_model

SUPPORTED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
JUDGE_CRITERIA = ("correctness", "completeness", "grounding")


def load_evaluator():
    return OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
        max_retries=5,
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
- Keep "reason" to one sentence of at most 30 words.
- Return JSON only, with this exact structure:
{{
  "correctness": 0.0,
  "completeness": 0.0,
  "grounding": 0.0,
  "reason": "One-sentence explanation"
}}
"""

    response = client.chat.completions.create(
        model=judge_model(),
        messages=[
            {
                "role": "system",
                "content": "You are a strict RAG evaluation judge. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=900,
        response_format={"type": "json_object"},
    )

    scores = json.loads(response.choices[0].message.content)

    for key in JUDGE_CRITERIA:
        value = scores.get(key)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"Invalid evaluator score for {key}: {value}")

    scores["overall_score"] = round(sum(scores[key] for key in JUDGE_CRITERIA) / 3, 3)
    return scores


def run_image_cases(cases, case_min):
    client = load_evaluator()
    results = []

    try:
        for index, case in enumerate(cases, start=1):
            image_path = PROJECT_ROOT / case["image"]
            record = {
                "id": case["id"],
                "type": case.get("type", ""),
                "task_type": case.get("task_type", ""),
                "question": case["question"],
                "image": case["image"],
                "ground_truth": case["ground_truth"],
                "answer": "",
                "retrieved_contexts": [],
                "status": "error",
            }

            print(f"\n[{index}/{len(cases)}] {case['id']}: {case['question']}")

            try:
                if not image_path.is_file():
                    raise FileNotFoundError(f"Image not found: {image_path}")

                mime_type, _ = mimetypes.guess_type(image_path.name)
                if mime_type not in SUPPORTED_MIME_TYPES:
                    raise ValueError(f"Unsupported image type: {mime_type}")

                response = ask_with_retry(
                    case["question"].strip(),
                    images=[(image_path.read_bytes(), mime_type)],
                )
                answer = (response.get("answer") or "").strip()
                contexts = response.get("sources") or []
                record["answer"] = answer
                record["retrieved_contexts"] = contexts

                if not answer:
                    record["status"] = "fail"
                    record["failure_reason"] = "Empty answer"
                elif not contexts:
                    record["status"] = "fail"
                    record["failure_reason"] = "No retrieved evidence"
                else:
                    scores = evaluate_answer(
                        client=client,
                        question=case["question"],
                        reference=case["ground_truth"],
                        answer=answer,
                        contexts=contexts,
                    )
                    record["scores"] = scores
                    below = [
                        f"{name} {scores[name]:.2f} < {floor}"
                        for name, floor in case_min.items()
                        if scores[name] < floor
                    ]
                    if below:
                        record["status"] = "fail"
                        record["failure_reason"] = ", ".join(below)
                    else:
                        record["status"] = "passed"

            except Exception as exc:
                record["status"] = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"

            print(f"  {record['status']}: {record.get('failure_reason') or record.get('error') or 'ok'}")
            if "scores" in record:
                print(f"  judge: {record['scores'].get('reason', '')}")
            results.append(record)

    finally:
        client.close()

    return results
