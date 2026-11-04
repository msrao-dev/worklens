"""Generate live answers for the text golden cases by calling the RAG pipeline."""

from evaluation.common import ask_with_retry

NOT_FOUND_ANSWER = "Information not found."


def generate_text_answers(cases):
    results = []

    for index, case in enumerate(cases, start=1):
        print(f"\n[{index}/{len(cases)}] {case['id']}: {case['question']}")

        record = {
            "id": case["id"],
            "type": case.get("type", ""),
            "question": case["question"],
            "ground_truth": case.get("ground_truth", ""),
            "answer": "",
            "retrieved_contexts": [],
            "status": "error",
        }

        try:
            response = ask_with_retry(case["question"])
            answer = (response.get("answer") or "").strip()
            contexts = response.get("sources") or []
            record["answer"] = answer
            record["retrieved_contexts"] = contexts

            # Unanswerable cases are expected to decline, so "not found" is fine there.
            expects_answer = "unanswerable" not in record["type"].lower()

            if not answer:
                record["status"] = "fail"
                record["failure_reason"] = "Empty answer"
            elif not contexts:
                record["status"] = "fail"
                record["failure_reason"] = "No retrieved evidence"
            elif answer == NOT_FOUND_ANSWER and expects_answer:
                record["status"] = "fail"
                record["failure_reason"] = "App answered 'Information not found.'"
            else:
                record["status"] = "answered"

        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"

        print(f"  {record['status']}: {record.get('failure_reason') or record.get('error') or 'ok'}")
        results.append(record)

    return results
