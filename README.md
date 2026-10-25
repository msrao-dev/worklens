BE deployment:


## set up
git clone https://github.com/msrao-dev/worklens.git worklens
cd worklens

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt



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
