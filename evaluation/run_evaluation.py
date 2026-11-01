import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.rag import ask_question

GOLDEN_DATA_PATH = PROJECT_ROOT / "evaluation" / "golden_questions.json"
OUTPUT_PATH = PROJECT_ROOT / "evaluation" / "image_evaluation_results.json"

MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def main():
    with GOLDEN_DATA_PATH.open("r", encoding="utf-8") as file:
        golden_questions = json.load(file)

    image_cases = [
        item for item in golden_questions
        if item.get("image")
    ]

    results = []

    if not image_cases:
        print("No image questions found.")
        return

    for index, item in enumerate(image_cases, start=1):
        item_id = item["id"]
        question = item["question"]
        image_path = PROJECT_ROOT / item["image"]

        print(f"\n{'=' * 70}")
        print(f"[{index}/{len(image_cases)}] {item_id}")
        print(f"Question: {question}")
        print(f"Image: {item['image']}")

        try:
            if not image_path.is_file():
                raise FileNotFoundError(f"Image not found: {image_path}")

            mime_type = MIME_TYPES.get(image_path.suffix.lower())
            if not mime_type:
                raise ValueError(
                    f"Unsupported image type: {image_path.suffix}"
                )

            image_bytes = image_path.read_bytes()

            result = ask_question(
                question,
                images=[(image_bytes, mime_type)],
            )

            answer = result.get("answer", "")
            contexts = result.get("retrieved_contexts", [])

            # Print the generated answer even if contexts are unavailable.
            print("\nGENERATED RESPONSE:")
            print(answer or "(No answer returned)")

            print(f"\nRetrieved contexts found: {len(contexts)}")

            if not contexts:
                print(
                    "WARNING: No retrieved contexts were found in the "
                    "ask_question() result. Check the return structure "
                    "used by the existing text evaluation runner."
                )

            status = "success" if answer and contexts else (
                "missing_contexts" if answer else "empty_response"
            )

            results.append({
                "id": item_id,
                "question": question,
                "answer": answer,
                "retrieved_contexts": contexts,
                "ground_truth": item.get("expected_answer"),
                "status": status,
            })

            print(f"\nStatus: {status}")

        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            print(f"\nERROR: {error_message}")

            results.append({
                "id": item_id,
                "question": question,
                "answer": "",
                "retrieved_contexts": [],
                "ground_truth": item.get("expected_answer"),
                "status": "error",
                "error": error_message,
            })

        # Save progress after every image question.
        OUTPUT_PATH.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"\nImage results saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
