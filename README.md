# TraceRAG: A RAG-based Software Traceability Link Recovery Tool

TraceRAG recovers traceability links between software artifacts (requirements, architecture
documents, UML models and source code) and keeps them meaningful as the artifacts change.
Candidate elements are retrieved by embedding similarity, and an LLM decides which pairs are real
links and explains why.

It is built on the approach of **LiSSA** ([Fuchß et al., ICSE 2025](#acknowledgements-and-reference))
and implements it as a configurable web application, extended with:

- dependency-aware link expansion along calls and inheritance in the code;
- summarisation and semantic enrichment of code and model elements before embedding;
- versioned analyses with change tracking, so links can be followed as the artifacts change.

## Features

| Feature | What it does |
|---|---|
| Links across artifact kinds | Requirements, architecture documents, UML models and code; either can be source or target |
| Evidence for every link | A confidence score and the classifier's explanation |
| Configurable granularity | Split into sentences or documents, methods or chunks, UML components; report links at method, class or file level |
| Versions and updates | Update one side by upload or from a GitHub commit; a new version only when something meaningful changed |
| Change report | Between any two versions, each link is valid, new, no longer found or broken, with the reason; changed files and elements with a line diff |
| Stable links | Links from the previous run are offered to the classifier again and follow their elements when positions or file names change |
| Results and export | Linked-artifact panels, trace explorer, traceability matrix, architecture diagram; CSV, Excel or PDF |
| Accounts and projects | Run without an account; sign in to save analyses into projects |

## How it works

<!-- TODO: to replace the text sketch below with the pipeline diagram -->

```
artifacts ──> split into elements ──> summarise code/model elements ──> embed
                                                                          │
   trace matrix <── roll up <── expand by dependencies <── classify <── retrieve top-k
```

Each side is split into elements by a preprocessor. Code and UML elements can get a one-sentence
summary, then all elements are embedded into a vector index per analysis. For each source element
the top-k most similar target elements are retrieved and the LLM decides, pair by pair, whether it
is a link. Links can then be extended along code dependencies and rolled up to the requested
level. Embeddings, summaries and classifier decisions are cached by content, so unchanged text is
never sent to a model twice.

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, Alembic |
| Database / vector index | PostgreSQL / ChromaDB |
| Embeddings | Ollama (`nomic-embed-text`) |
| Classification and summaries | Groq API |
| Code parsing | tree-sitter (Java, Python, JavaScript, TypeScript) |
| Frontend | React, Vite, react-router |
| Tests | pytest, Playwright |

## Getting started

**Prerequisites** (versions used in development): Python 3.11, Node.js 22 (Vite needs 20.19 or
newer), PostgreSQL 18, Ollama, and a Groq API key from [console.groq.com](https://console.groq.com).

```bash
git clone https://github.com/SharminNusrat/TraceRAG.git
cd TraceRAG
createdb tracerag
ollama pull nomic-embed-text
```

**Backend.** Create `backend/.env`:

```ini
DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/tracerag
GROQ_API_KEYS=<your-groq-key>            # several keys can be given, comma separated
SECRET_KEY=<a-long-random-string>        # at least 32 characters

# Optional: lets users connect their GitHub account
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
FRONTEND_URL=http://localhost:5173
```

Then install and start it from the `backend/` folder:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

The API runs on `http://localhost:8000` (`/health` answers `{"status":"ok"}`). The tables are
created automatically: migrations run every time the backend starts.

**Frontend.** In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

**GitHub connection (optional).** Create a GitHub OAuth app with the callback URL
`http://localhost:8000/github/oauth/callback` and put its client ID and secret in `backend/.env`.
Without it, a repository can still be connected with a personal access token.

## Using TraceRAG

**1. Start an analysis.** Upload the two artifacts, choose which one to trace from and which to
trace to, review the settings and run.

<!-- TODO screenshot: New Analysis, upload step with both artifacts added -->

**2. Read the results.** Browse the links, open the explanation of any link, view the traceability
matrix, and export it.

<!-- TODO screenshot: Results page of a real run (linked artifacts or matrix) -->

**3. Save.** Sign in and save the run into a project. This creates an analysis at version 1.

**4. Update.** When an artifact changes, open the analysis and update that side, by upload or from
GitHub. The analysis is run again as the next version.

<!-- TODO screenshot: Analysis page with both sides, or the Update dialog showing the change summary -->

**5. Compare.** Open **Trace links**, choose two versions, and read what happened to each link.
The **Changes** tab lists the files and elements that differ.

<!-- TODO screenshot: Trace Links page with the five groups; optionally the Changes tab with a diff -->

**Supported files:** `.txt`, `.pdf`, `.docx` for requirements and architecture documents; `.java`,
`.py`, `.js`, `.ts` (or a `.zip`) for code; `.uml`, `.xmi` for UML models. Uploads are limited to
30 MB.

## Tests

Backend tests need PostgreSQL and `backend/.env`. They create their own database (`tracerag_test`)
and use fake models, so no Ollama, Groq or GitHub access is required. UI tests start their own
backend and frontend on free ports.

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest

cd ../frontend
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
```

## Notes and limitations

- Link quality depends on the embedding model and the LLM; the tests check behaviour, not accuracy.
- Classification uses the Groq API, so an analysis needs an internet connection and a valid key.
- Stored files and vector indexes are kept under `backend/app_data/` and `backend/chroma_data/`.
  Start the server from `backend/` so they are found.
- An update takes one side at a time.
- There is no Docker setup or installer yet.

## Acknowledgements and reference

TraceRAG follows the approach introduced by LiSSA, developed by the
[ArDoCo](https://github.com/ardoco) project at the Karlsruhe Institute of Technology. The original
implementation is at [github.com/ardoco/lissa](https://github.com/ardoco/lissa) (MIT License).
TraceRAG is an independent implementation and is not affiliated with or endorsed by its authors.

> Dominik Fuchß, Tobias Hey, Jan Keim, Haoyu Liu, Niklas Ewald, Tobias Thirolf, and Anne Koziolek.
> "LiSSA: Toward Generic Traceability Link Recovery Through Retrieval-Augmented Generation."
> In *2025 IEEE/ACM 47th International Conference on Software Engineering (ICSE)*, Ottawa, Canada,
> pp. 1396–1408. IEEE, 2025. https://doi.org/10.1109/ICSE55347.2025.00186
