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
Runs the golden questions in `evaluation/golden_questions.json` (text + image) through the real
RAG pipeline, scores them (RAGAS for text, an LLM judge for images) and fails when quality drops
below `evaluation/thresholds.json`. Needs `LLM_PROVIDER=groq` and `GROQ_API_KEY` in `.env`.

```
python -m evaluation.run_suite --suite smoke     # few fast cases
python -m evaluation.run_suite --suite sanity    # every case
python -m evaluation.run_suite --suite smoke --only image
```

The report is written to `evaluation/reports/report.md` (and `report.json`).

On GitHub Actions the "WorkLens Evaluation" workflow runs smoke on every PR, sanity on merge to
main and nightly, and either suite on demand (Actions → WorkLens Evaluation → Run workflow).
The report shows on the run's Summary page and as the `evaluation-<suite>-report` artifact.
Cases whose `source_documents` are not in `backend/uploads` are skipped and listed in the report.
