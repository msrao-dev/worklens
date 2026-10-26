# WorkLens — Angular front end

The Employee Knowledge Assistant UI, built as an Angular 17 standalone-component app.
The app includes a sample knowledge base in `src/app/worklens.data.ts` for mock
mode and can also call the FastAPI RAG backend for real answers.

## What you need first

- **Node.js 18.13+ or 20+** — download the LTS installer from https://nodejs.org and run it.
  (Unlike the single HTML prototype, Angular needs Node to build and run.)

Check it installed, in a terminal / PowerShell:

```
node -v
npm -v
```

### Frontend Environment Variables

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `API_URL` | string | `'http://127.0.0.1:8000'` | Base URL for FastAPI backend; when deploying the Docker image, set this on the container to configure the backend URL at runtime |
| `useMock` | boolean | `false` | When `true`, bypasses the API and uses the local mock KB for instant UI testing |

For example, set `API_URL` to the backend's browser-accessible URL when starting the frontend container. The built-in localhost URL remains the fallback when no runtime value is provided.

## Run it (Windows, macOS or Linux)

From inside this folder:

```
npm install        # one-time: downloads Angular and dependencies
npm start          # builds and serves, then opens http://localhost:4200
```

`npm start` runs `ng serve --open`, so it launches the app in your browser and
live-reloads whenever you edit a file. Stop it with Ctrl+C.

> First run note: `npm install` needs internet and takes a few minutes. If you
> don't already have the Angular CLI globally, you don't need it — the local
> copy in `node_modules` is used by the npm scripts.

## Build for deployment

```
npm run build
```

Outputs a static, optimized site to `dist/worklens/` that you can host on any
web server or static host.

### Docker (PowerShell)

From the repository root:

```powershell
docker build -t worklens-frontend -f .\frontend\Dockerfile .\frontend
docker run --rm -p 8080:80 -e API_URL=https://api.example.com worklens-frontend
```

Alternatively, from inside the `frontend` folder:

```powershell
docker build -t worklens-frontend .
docker run --rm -p 8080:80 -e API_URL=https://api.example.com worklens-frontend
```

Replace `https://api.example.com` with your backend's browser-accessible base
URL, then open http://localhost:8080.

The build context (the last argument to `docker build`) must be the `frontend`
folder: the Dockerfile copies `src`, package manifests, and configuration files
relative to that context. Selecting the Dockerfile with `-f` does not change
the context. Using the repository root as the context causes missing-source
errors such as `"/src": not found`; the root `.dockerignore` also excludes
`frontend` because it is intended for backend builds.

## Project layout

```
src/
  index.html              Page shell + Google Fonts
  main.ts                 Bootstraps the standalone AppComponent
  styles.css              The full visual system (global)
  app/
    app.component.ts       Component logic: state, matching, theme, actions
    app.component.html     The chat UI template
    safe-html.pipe.ts      Renders trusted answer/passage HTML
    worklens.data.ts       Sample knowledge base + suggestions + shortcuts
```

## Where the real backend plugs in

The chat sends text-only questions as JSON to `POST /query`:

```json
{"question": "How many leave days do I have?"}
```

When an image is attached, the browser sends `multipart/form-data` to the same
endpoint with a `question` field and one or more `image` file fields. The backend
validates each image (JPEG, PNG, or WebP; 10 MB maximum per image; at most three
images; 14 MB combined raw image data) and sends valid image bytes inline as
Base64 data URLs to the configured Groq vision model. The Groq API key remains
on the backend.

Configure the backend with `LLM_PROVIDER=groq`, `GROQ_MODEL=qwen/qwen3.8-27b`,
and `GROQ_API_KEY`. Text-only requests continue to use the existing JSON
interface and RAG behavior. Image queries include retrieved handbook context;
visual observations are not treated as evidence of company policy.

The existing `/ingest` endpoint accepts PDF, DOCX, and TXT files and stores
extracted text in ChromaDB. PDF extraction is text-only (`page.get_text()`):
embedded charts, screenshots, and scanned pages may not be represented in
retrieved context. PDF page-image processing is not part of image-aware chat.

## Features in this build

- Natural-language Q&A over the (sample) knowledge base
- Source citations — click any source to see the exact highlighted passage
- Honest "I don't know" + knowledge-gap logging for out-of-scope questions
- Role / location-aware framing ("Answered for Engineering · Bengaluru")
- Freshness stamps (sources over a year old are flagged "verify")
- Confidence level, 👍/👎 feedback, copy, "ask a human" escalation
- Suggested questions and personal shortcuts
- Light / dark theme (remembered per browser) and mobile layout
