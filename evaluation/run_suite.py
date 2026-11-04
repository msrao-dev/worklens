"""Run the WorkLens response-quality suite and write one report for text and image cases.

Usage, from the repo root:
    python -m evaluation.run_suite --suite smoke
    python -m evaluation.run_suite --suite sanity
    python -m evaluation.run_suite --suite smoke --only image
    python -m evaluation.run_suite --suite smoke --golden evaluation/golden_questions.json

Writes evaluation/reports/report.md and report.json, and appends the Markdown
report to the GitHub job summary when running in Actions. Exits 1 when any case
fails or errors, or when a text metric average is below its threshold.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from evaluation.common import (
    EVAL_GROQ_API_KEY,
    GOLDEN_FILE,
    REPORT_DIR,
    THRESHOLDS_FILE,
    judge_model,
    load_json,
)

MAX_ANSWER_CHARS = 1500


def parse_args():
    parser = argparse.ArgumentParser(description="Run the WorkLens evaluation suite.")
    parser.add_argument(
        "--suite",
        choices=("smoke", "sanity"),
        default=os.getenv("SUITE", "smoke"),
        help="smoke = a few fast cases, sanity = every golden case",
    )
    parser.add_argument("--only", choices=("all", "text", "image"), default="all")
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(os.getenv("EVAL_GOLDEN_FILE") or GOLDEN_FILE),
        help="golden questions file (default: evaluation/golden_data_v1.json)",
    )
    return parser.parse_args()


def check_environment():
    problems = []
    if not EVAL_GROQ_API_KEY:
        problems.append("EVAL_GROQ_API_KEY is not set (the main GROQ_API_KEY is never used for evaluation)")
    if os.getenv("LLM_PROVIDER", "ollama").lower() != "groq":
        problems.append("LLM_PROVIDER must be 'groq' (image questions need a Groq vision model)")
    if problems:
        raise SystemExit("Cannot run the evaluation: " + "; ".join(problems))


def index_documents():
    """Index the documents folder exactly as the app indexes its uploads on startup."""
    from backend.app import _reindex_missing_uploads

    status = _reindex_missing_uploads()["index_status"]
    print(f"Indexed {status['indexed_chunks']} chunks from: {', '.join(status['sources'])}")
    if not status["indexed_chunks"]:
        raise SystemExit(f"No documents were indexed; check {os.environ['UPLOAD_DIR']}.")
    return [Path(source).stem for source in status["sources"]]


def _document_words(name):
    """'LAPTOP POLICY (1)' -> {'laptop', 'policy'}"""
    name = re.sub(r"\(\d+\)", " ", name.lower())
    return set(re.findall(r"[a-z0-9]+", name))


def is_indexed(document, indexed_documents):
    """Golden names and file names differ ('Laptop Issuance Policy' vs 'LAPTOP POLICY (1)'),
    so match when one name's words contain the other's, sharing at least two words."""
    wanted = _document_words(document)
    for indexed in indexed_documents:
        words = _document_words(indexed)
        if len(wanted & words) >= 2 and (wanted <= words or words <= wanted):
            return True
    return False


def select_cases(golden_file, suite, only, indexed_documents):
    selected, skipped = [], []
    for case in load_json(golden_file):
        if suite not in case.get("suites", []):
            continue
        kind = "image" if case.get("image") else "text"
        if only not in ("all", kind):
            continue
        missing = [
            doc for doc in case.get("source_documents", [])
            if not is_indexed(doc, indexed_documents)
        ]
        if missing:
            skipped.append({"id": case["id"], "reason": "Not indexed: " + ", ".join(missing)})
            continue
        selected.append(case)
    return selected, skipped


def git_commit():
    sha = os.getenv("GITHUB_SHA")
    if sha:
        return sha[:7]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_report(suite, text_records, image_records, skipped, thresholds, metric_names):
    from evaluation.score_evaluation import metric_averages

    averages = metric_averages(text_records, metric_names)
    text_gates = {
        name: {
            "average": averages[name],
            "threshold": thresholds["text"]["mean"][name],
            "passed": averages[name] is not None and averages[name] >= thresholds["text"]["mean"][name],
        }
        for name in metric_names
    } if text_records else {}

    cases = text_records + image_records
    failed_cases = [r["id"] for r in cases if r["status"] != "passed"]
    failed_gates = [name for name, gate in text_gates.items() if not gate["passed"]]

    return {
        "suite": suite,
        "commit": git_commit(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "passed": bool(cases) and not failed_cases and not failed_gates,
        "failed_cases": failed_cases,
        "failed_gates": failed_gates,
        "text_metrics": metric_names,
        "text_gates": text_gates,
        "text_cases": text_records,
        "image_cases": image_records,
        "skipped": skipped,
    }


def _score(record, name):
    value = record.get("scores", {}).get(name)
    return f"{value:.2f}" if isinstance(value, (int, float)) else "-"


def _reason(record):
    text = record.get("failure_reason") or record.get("error") or ""
    return text.replace("|", "/").replace("\n", " ")[:200]


def _status(record):
    return {"passed": "✅ passed", "fail": "❌ fail"}.get(record["status"], "⚠️ " + record["status"])


def render_markdown(report):
    metric_names = report["text_metrics"]
    lines = [
        f"# WorkLens evaluation: {report['suite']} — {'✅ PASSED' if report['passed'] else '❌ FAILED'}",
        "",
        f"Commit `{report['commit']}` · `{report['golden_file']}` · {report['generated_at']} · "
        f"{len(report['text_cases'])} text, {len(report['image_cases'])} image, "
        f"{len(report['skipped'])} skipped",
        "",
    ]

    if report["text_gates"]:
        lines += ["## Text quality (RAGAS averages)", "", "| Metric | Average | Threshold | Result |", "|---|---|---|---|"]
        for name, gate in report["text_gates"].items():
            average = "-" if gate["average"] is None else f"{gate['average']:.3f}"
            lines.append(f"| {name} | {average} | {gate['threshold']} | {'✅' if gate['passed'] else '❌'} |")
        lines.append("")

    if report["text_cases"]:
        header = " | ".join(metric_names)
        lines += [
            "## Text cases", "",
            f"| ID | Type | Status | {header} | Reason |",
            "|---" * (len(metric_names) + 4) + "|",
        ]
        for record in report["text_cases"]:
            scores = " | ".join(_score(record, name) for name in metric_names)
            lines.append(f"| {record['id']} | {record['type']} | {_status(record)} | {scores} | {_reason(record)} |")
        lines.append("")

    if report["image_cases"]:
        lines += [
            "## Image cases", "",
            "| ID | Task | Status | correctness | completeness | grounding | Reason |",
            "|---|---|---|---|---|---|---|",
        ]
        for record in report["image_cases"]:
            lines.append(
                f"| {record['id']} | {record['task_type']} | {_status(record)} | "
                f"{_score(record, 'correctness')} | {_score(record, 'completeness')} | "
                f"{_score(record, 'grounding')} | {_reason(record)} |"
            )
        lines.append("")

    if report["skipped"]:
        lines += ["## Skipped", ""]
        lines += [f"- {item['id']}: {item['reason']}" for item in report["skipped"]]
        lines.append("")

    lines += ["## Answers", ""]
    for record in report["text_cases"] + report["image_cases"]:
        answer = record["answer"] or "(no answer)"
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS] + " …"
        judge = record.get("scores", {}).get("reason")
        lines += [
            f"<details><summary>{record['id']} — {_status(record)}</summary>",
            "",
            f"**Question:** {record['question']}",
            "",
            f"**Answer:** {answer}",
            "",
            f"**Expected:** {record['ground_truth']}",
            "",
        ]
        if judge:
            lines += [f"**Judge:** {judge}", ""]
        lines += ["</details>", ""]

    return "\n".join(lines)


def write_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report)
    (REPORT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "report.md").write_text(markdown, encoding="utf-8")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(markdown + "\n")


def main():
    args = parse_args()
    check_environment()
    thresholds = load_json(THRESHOLDS_FILE)

    indexed_documents = index_documents()
    print(f"Golden file: {args.golden}")
    cases, skipped = select_cases(args.golden, args.suite, args.only, indexed_documents)
    text_cases = [case for case in cases if not case.get("image")]
    image_cases = [case for case in cases if case.get("image")]
    print(f"Suite '{args.suite}': {len(text_cases)} text, {len(image_cases)} image, {len(skipped)} skipped")

    text_records, image_records, metric_names = [], [], []
    print(f"Judge model: {judge_model()}")

    if text_cases:
        from evaluation.run_evaluation import generate_text_answers
        from evaluation.score_evaluation import SUITE_METRICS, score_text

        metric_names = SUITE_METRICS[args.suite]
        print("\n=== Text cases: generating answers ===")
        text_records = generate_text_answers(text_cases)
        print(f"\n=== Text cases: RAGAS scoring ({', '.join(metric_names)}) ===")
        text_records = asyncio.run(
            score_text(text_records, thresholds["text"]["case_min"], metric_names)
        )

    if image_cases:
        from evaluation.run_image_evaluation import run_image_cases

        print("\n=== Image cases ===")
        image_records = run_image_cases(image_cases, thresholds["image"]["case_min"])

    report = build_report(args.suite, text_records, image_records, skipped, thresholds, metric_names)
    report["golden_file"] = args.golden.name
    write_report(report)

    print(f"\nReport: {REPORT_DIR / 'report.md'}")
    if report["passed"]:
        print("RESULT: PASSED")
        return

    print("RESULT: FAILED")
    if report["failed_cases"]:
        print("  Cases not passed: " + ", ".join(report["failed_cases"]))
    if report["failed_gates"]:
        print("  Metric averages below threshold: " + ", ".join(report["failed_gates"]))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
