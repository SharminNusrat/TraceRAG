# TraceRAG

TraceRAG recovers traceability links between software artifacts (requirements, architecture
documents, UML models and source code) and keeps those links meaningful as the artifacts change.

It combines retrieval with an LLM classifier: every element of one artifact is embedded, its most
similar counterparts are retrieved, and a language model decides which of them are real links and
explains why. Each analysis keeps a version history, so after an update you can see which links
still hold, which are new, which stopped being found and which broke.

## Features

- **Links across artifact kinds.** Trace requirements, architecture documents, UML models and
  source code to each other. Either kind can be the source or the target.
- **Evidence for every link.** Each link has a confidence score and the classifier's explanation.
- **Configurable granularity.** Split requirements into sentences or whole documents, code into
  methods or chunks, UML models into components, and report links at the level you choose
  (method, class or file, for example).
- **Dependency-aware recovery.** For a code target, links can be extended along calls and
  inheritance.
- **Versions and updates.** Update one side of an analysis by uploading its current files, or by
  fetching the latest commit of a GitHub repository. A new version is created only when something
  meaningful changed.
- **Change report.** Between any two versions, every link is reported as valid, new, no longer
  found or broken, with the reason. Changed files and elements are listed with a line diff.
- **Stable links.** Links confirmed by the previous run are offered to the classifier again, and
  follow their elements when positions or file names change.
- **Results and export.** Linked-artifact panels, a trace explorer, a traceability matrix and an
  architecture diagram; export to CSV, Excel or PDF.
- **Accounts and projects.** Analyses can be run without an account and saved into projects after
  signing in.

## How it works

<!-- TODO: to replace the text sketch below with the pipeline diagram -->

```
artifacts ──> split into elements ──> summarise code/model elements ──> embed
                                                                          │
   trace matrix <── roll up <── expand by dependencies <── classify <── retrieve top-k
```

1. **Load and split.** Each side is read and split into elements by a preprocessor.
2. **Summarise.** Elements that are not prose (code, UML) can get a one-sentence summary.
3. **Embed and index.** Elements are embedded and stored in a vector index per analysis.
4. **Retrieve and classify.** For each source element, the top-k most similar target elements
   are retrieved, and the LLM decides for each pair whether it is a link.
5. **Expand and aggregate.** Links can be extended along code dependencies, then rolled up to
   the requested output level.

Embeddings, summaries and classifier decisions are cached by content, so unchanged text is never
sent to a model twice.

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Alembic |
| Database | PostgreSQL |
| Vector index | ChromaDB |
| Embeddings | Ollama (`nomic-embed-text`) |
| Classification and summaries | Groq API |
| Code parsing | tree-sitter (Java, Python, JavaScript, TypeScript) |
| Frontend | React, Vite, react-router |
| Tests | pytest, Playwright |

## Getting started

### Prerequisites

| Software | Version used in development |
|---|---|
| Python | 3.11 |
| Node.js | 22 (Vite needs 20.19 or newer) |
| PostgreSQL | 18 |
| Ollama | with the model `nomic-embed-text` |
| Groq API key | from [console.groq.com](https://console.groq.com) |

### 1. Clone

```bash
git clone https://github.com/SharminNusrat/TraceRAG.git
cd TraceRAG
```

### 2. Database and embedding model

```bash
createdb tracerag
ollama pull nomic-embed-text
```

The tables are created automatically: the backend runs its migrations every time it starts.

### 3. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Create `backend/.env`:

```ini
DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/tracerag
GROQ_API_KEYS=<your-groq-key>            # several keys can be given, comma separated
SECRET_KEY=<a-long-random-string>        # at least 32 characters

# Optional: lets users connect their GitHub account
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
FRONTEND_URL=http://localhost:5173
```

Start the server from the `backend/` folder:

```bash
uvicorn main:app --reload
```

The API runs on `http://localhost:8000`; `http://localhost:8000/health` answers `{"status":"ok"}`.

### 4. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

### GitHub connection (optional)

To let users connect their GitHub account, create a GitHub OAuth app with the callback URL
`http://localhost:8000/github/oauth/callback` and put its client ID and secret in `backend/.env`.
Without it, a repository can still be connected with a personal access token.

## Using TraceRAG

### 1. Start an analysis

Upload the two artifacts, choose which one to trace from and which to trace to, review the
settings and run.

<!-- TODO screenshot: New Analysis, upload step with both artifacts added -->

### 2. Read the results

Browse the links, open the explanation of any link, view the traceability matrix, and export it.

<!-- TODO screenshot: Results page of a real run (linked artifacts or matrix) -->

### 3. Save

Sign in and save the run into a project. This creates an analysis at version 1.

### 4. Update

When an artifact changes, open the analysis and update that side, by upload or from GitHub. The
analysis is run again as the next version.

<!-- TODO screenshot: Analysis page with both sides, or the Update dialog showing the change summary -->

### 5. Compare

Open **Trace links**, choose two versions, and read what happened to each link. The **Changes**
tab lists the files and elements that differ.

<!-- TODO screenshot: Trace Links page with the five groups; optionally the Changes tab with a diff -->

### Supported files

`.txt`, `.pdf`, `.docx` for requirements and architecture documents; `.java`, `.py`, `.js`,
`.ts` (or a `.zip`) for code; `.uml`, `.xmi` for UML models. Uploads are limited to 30 MB.

## Tests

Backend tests need PostgreSQL and `backend/.env`. They create their own database
(`tracerag_test`) and use fake models, so no Ollama, Groq or GitHub access is required.

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

UI tests start their own backend and frontend on free ports:

```bash
cd frontend
npx playwright install chromium
set E2E_PYTHON=<path to the backend's python>     # Linux/macOS: export E2E_PYTHON=...
npx playwright test
```

The full test report is in [backend/tests/TEST_REPORT.md](backend/tests/TEST_REPORT.md).

## Project structure

```
backend/
  api/          HTTP routes, background jobs, request and response schemas
  core/         pipeline, preprocessors, classifiers, caches, versions, change report
  migrations/   Alembic database migrations
  tests/        unit, service and integration tests
frontend/
  src/          React application (pages, features, components)
  e2e/          Playwright UI tests and their screenshots
docs/           design facts, API endpoints, UI and installation notes
```

More detail: [docs/DESIGN_FACTS.md](docs/DESIGN_FACTS.md), [docs/API_ENDPOINTS.md](docs/API_ENDPOINTS.md),
[docs/UI_FACTS.md](docs/UI_FACTS.md), [docs/INSTALL_FACTS.md](docs/INSTALL_FACTS.md).

## Notes and limitations

- The link quality depends on the embedding model and the LLM; the tests check behaviour, not
  accuracy.
- Classification uses the Groq API, so an analysis needs an internet connection and a valid key.
- Stored files and vector indexes are kept on disk under `backend/app_data/` and
  `backend/chroma_data/`. Start the server from `backend/` so they are found.
- An update takes one side at a time.
- There is no Docker setup or installer yet.
