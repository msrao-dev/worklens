BE deployment:


## set up
git clone https://github.com/msrao-dev/worklens.git worklens
cd worklens

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

install following system packages for read aloud feature: espeak-ng libespeak-ng1

set env vars:
```
CORS_ORIGINS=http://<FRONTEND_IP_OR_DOMAIN>
UPLOAD_DIR=./backend/uploads
CHROMA_PATH=./backend/chroma_db
USER_DB_PATH=./backend/users.db
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

# Mandatory Auth Configuration
## following secret can be generated from following command: openssl rand -base64 32
JWT_SECRET=your_super_secret_key_here
HR_EMAIL=<email-id-of-HR-user>
HR_INITIAL_PW=<initial-password-for-HR-user>

LLM_PROVIDER=groq

GROQ_API_KEY=<grok-api-key>
GROQ_MODEL=qwen/qwen3.8-27b
```

## serving application
uvicorn backend.app:app --host 0.0.0.0 --port 8000

## response-quality evaluation
Runs the golden questions in `evaluation/golden_data_v1.json` (text + image) through the real
RAG pipeline, scores them (RAGAS for text, an LLM judge for images) and fails when quality drops
below `evaluation/thresholds.json`. Needs `LLM_PROVIDER=groq` and `EVAL_GROQ_API_KEY` in `.env`:
a key from a separate Groq account, so evaluation never spends the live app's quota. The main
`GROQ_API_KEY` is never used by the evaluation.

```
python -m evaluation.run_suite --suite smoke     # few fast cases
python -m evaluation.run_suite --suite sanity    # every case
python -m evaluation.run_suite --suite smoke --only image
python -m evaluation.run_suite --suite smoke --golden evaluation/golden_questions.json   # older set
```

It indexes every PDF in `documents/` into its own Chroma folder (`evaluation/chroma_db`), so your
local app database is untouched. Golden `source_documents` names are matched loosely to file names
(e.g. "Laptop Issuance Policy" → `LAPTOP POLICY (1).pdf`). Optional `.env` settings:
`EVAL_DOCUMENTS_DIR`, `EVAL_CHROMA_PATH`, `EVAL_GOLDEN_FILE`, `EVAL_JUDGE_MAX_TOKENS` (use ~2048
with a gpt-oss judge), `EVAL_ANSWER_MAX_WORDS` (default 100: the eval asks the app for answers of
about this many words to save tokens; `0` turns it off. The live app is never limited).

The report is written to `evaluation/reports/report.md` (and `report.json`).

On GitHub Actions the "WorkLens Evaluation" workflow is manual only: Actions → WorkLens Evaluation
→ Run workflow, then pick the branch and suite. It needs the `EVAL_GROQ_API_KEY` repo secret.
Answers come from `GROQ_MODEL`; scoring uses `EVAL_JUDGE_MODEL` (repo variable / `.env`) when set,
so grading does not use up the answer model's daily Groq quota.
The report shows on the run's Summary page and as the `evaluation-<suite>-report` artifact.
Cases whose `source_documents` match no file in `documents/` are skipped and listed in the report.
