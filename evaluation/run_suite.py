"""Run the WorkLens response-quality suite and write one report for text and image cases.

Usage, from the repo root:
    python -m evaluation.run_suite --suite smoke
    python -m evaluation.run_suite --suite sanity
    python -m evaluation.run_suite --suite smoke --only image

Writes evaluation/reports/report.md and report.json, and appends the Markdown
report to the GitHub job summary when running in Actions. Exits 1 when any case
fails or errors, or when a text metric average is below its threshold.
"""

import argparse
import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from evaluation.common import GOLDEN_FILE, REPORT_DIR, THRESHOLDS_FILE, load_json

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
    return parser.parse_args()


def check_environment():
    problems = []
    if not os.getenv("GROQ_API_KEY"):
        problems.append("GROQ_API_KEY is not set")
    if os.getenv("LLM_PROVIDER", "ollama").lower() != "groq":
        problems.append("LLM_PROVIDER must be 'groq' (image questions need a Groq vision model)")
    if problems:
        raise SystemExit("Cannot run the evaluation: " + "; ".join(problems))


def index_documents():
    """Index backend/uploads exactly as the app does on startup."""
    from backend.app import _reindex_missing_uploads

    status = _reindex_missing_uploads()["index_status"]
    print(f"Indexed {status['indexed_chunks']} chunks from: {', '.join(status['sources'])}")
    if not status["indexed_chunks"]:
        raise SystemExit("No documents were indexed; check UPLOAD_DIR and backend/uploads.")
    return {Path(source).stem for source in status["sources"]}


def select_cases(suite, only, indexed_documents):
    selected, skipped = [], []
    for case in load_json(GOLDEN_FILE):
        if suite not in case.get("suites", []):
            continue
        kind = "image" if case.get("image") else "text"
        if only not in ("all", kind):
            continue
        missing = [doc for doc in case.get("source_documents", []) if doc not in indexed_documents]
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


def build_report(suite, text_records, image_records, skipped, thresholds):
    from evaluation.score_evaluation import metric_averages

    averages = metric_averages(text_records)
    text_gates = {
        name: {
            "average": averages.get(name),
            "threshold": minimum,
            "passed": averages.get(name) is not None and averages[name] >= minimum,
        }
        for name, minimum in thresholds["text"]["mean"].items()
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
    from evaluation.score_evaluation import METRIC_NAMES

    lines = [
        f"# WorkLens evaluation: {report['suite']} — {'✅ PASSED' if report['passed'] else '❌ FAILED'}",
        "",
        f"Commit `{report['commit']}` · {report['generated_at']} · "
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
        header = " | ".join(METRIC_NAMES)
        lines += [
            "## Text cases", "",
            f"| ID | Type | Status | {header} | Reason |",
            "|---" * (len(METRIC_NAMES) + 4) + "|",
        ]
        for record in report["text_cases"]:
            scores = " | ".join(_score(record, name) for name in METRIC_NAMES)
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
    cases, skipped = select_cases(args.suite, args.only, indexed_documents)
    text_cases = [case for case in cases if not case.get("image")]
    image_cases = [case for case in cases if case.get("image")]
    print(f"Suite '{args.suite}': {len(text_cases)} text, {len(image_cases)} image, {len(skipped)} skipped")

    text_records, image_records = [], []

    if text_cases:
        from evaluation.run_evaluation import generate_text_answers
        from evaluation.score_evaluation import score_text

        print("\n=== Text cases: generating answers ===")
        text_records = generate_text_answers(text_cases)
        print("\n=== Text cases: RAGAS scoring ===")
        text_records = asyncio.run(score_text(text_records, thresholds["text"]["case_min"]))

    if image_cases:
        from evaluation.run_image_evaluation import run_image_cases

        print("\n=== Image cases ===")
        image_records = run_image_cases(image_cases, thresholds["image"]["case_min"])

    report = build_report(args.suite, text_records, image_records, skipped, thresholds)
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
