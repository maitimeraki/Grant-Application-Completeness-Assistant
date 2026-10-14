# Grant Application Completeness Assistant

AI-assisted completeness review of draft funding applications against grant
guidelines. The system extracts submission requirements from a guideline,
maps each requirement to evidence in the draft application, flags gaps, and
produces clarification questions — with a human review step for every AI
suggestion.

> **Disclaimer:** this is a completeness review aid, not a funding-eligibility
> or legal determination. Every AI mapping is *suggested* until a human
> confirms, corrects, or rejects it.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + TypeScript + Vite + TanStack Query + React Router |
| Backend | FastAPI (Python 3.12+) + SQLAlchemy 2 + Alembic |
| Database | SQLite (file-based; volume-mounted in Docker) |
| LLM | Any LiteLLM-supported provider (default: OpenAI `gpt-4o-mini`) |
| Tests | pytest (backend), tsc + vite build gate (frontend) |
| Packaging | Multi-stage Docker image; GitHub Actions CI/CD |

## Setup (local development)

Prerequisites: Python 3.12+, Node 20+.

```powershell
# Windows — one command (creates venv, installs deps, seeds .env, starts both servers)
.\start.ps1
```

Manual equivalent:

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
cp .env.example .env                            # then add your LLM_API_KEY
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                                     # http://localhost:5173, proxies /api to :8000
```

Without an `LLM_API_KEY` the app still works in **manual review** mode
(create assessments with "Run AI review" unchecked; add requirements and
evidence by hand). With a key, the AI pipeline runs automatically.

## Architecture

```
frontend (React SPA)                 backend (FastAPI)
┌─────────────────────┐   /api/*   ┌──────────────────────────────────┐
│ Documents page      │──────────▶│ routers: documents, assessments, │
│ New assessment page │            │   health                         │
│ Assessment workspace│◀──────────│ services:                        │
│  - requirement list │   JSON    │  documents  (upload/paste, PDF/  │
│  - evidence review  │            │              DOCX/MD/TXT parse) │
│  - claims/questions │            │  retrieval  (lexical candidate   │
│  - AI run log       │            │              scoring)           │
└─────────────────────┘            │  quotes     (verbatim-quote     │
                                   │              verification)      │
                                   │  llm        (LiteLLM JSON-mode  │
                                   │              calls + call logs) │
                                   │  pipeline   (6-stage workflow)  │
                                   │  summary / completeness /       │
                                   │  staleness (deterministic)      │
                                   └───────────┬──────────────────────┘
                                               │ SQLAlchemy
                                        ┌──────▼──────┐   ┌──────────────┐
                                        │  SQLite DB  │   │ file storage │
                                        │ (Alembic)   │   │ (uploads)    │
                                        └─────────────┘   └──────────────┘
```

### AI workflow (6 stages, `backend/app/services/pipeline.py`)

1. **extract_requirements** — chunk the guideline, ask the LLM for structured
   requirements (mandatory/recommended/ambiguous). Every `basis_quote` is
   verified **verbatim** against the guideline; unverifiable quotes demote the
   requirement to `ambiguous`.
2. **map_requirements** — for each requirement, score candidate passages
   (lexical retrieval), let the LLM judge coverage, then verify each evidence
   quote against the application. Hallucinated quotes are kept visible but
   marked *unverified*.
3. **gap_analysis** — deterministic pass: no verified citation ⇒ weakness.
4. **claims** — extract evidence-dependent claims; judge support from supplied
   supporting-document metadata only (absence ⇒ `unsupported`, never assumed).
5. **questions** — generate prioritised clarification questions for gaps.
6. **finalize** — status → `review`; results wait for human approval.

Human-in-the-loop: mappings start as `suggested`. Reviewers **confirm /
correct / reject** each one (corrections must quote the application verbatim —
checked server-side), override requirement basis, mark N/A, accept/dispute
claims, and answer questions. Re-runs carry forward reviewed mappings when the
underlying documents are unchanged.

### Structured logs

- **AI-workflow log** — every LLM call (stage, model, attempt, success,
  latency, token usage, error) is persisted to the `llm_call_logs` table and
  exposed at `GET /api/assessments/{id}/llm-logs`; the UI shows it as a
  collapsible "AI run log" on the assessment page.
- **Application log** — one-line timestamped records via stdlib logging
  (`LOG_LEVEL` env var).
- **Audit log** — human review actions (confirm/correct/reject, basis
  overrides) record before/after snapshots in the `audit_log` table.

## Completed scope

- Document intake: PDF / DOCX / Markdown / plain-text upload or paste;
  immutable versioning with content hashes; extraction warnings surfaced.
- LLM pipeline (above) with quote-hallucination verification on both the
  guideline and application sides.
- Human review workspace: requirement filters, evidence citations with
  verbatim verification, confirm/correct/reject, manual requirements and
  manual evidence mapping, supporting-doc checklist, claims accept/dispute,
  clarification-question answers.
- Deterministic completion scoring, staleness detection when source documents
  gain new versions, re-run with requirement/mapping carry-forward.
- Markdown summary export with disclaimer.
- Loading / empty / validation / success (toast) / failure states across the
  UI; pipeline progress with live refresh.
- Structured AI-workflow logs + audit trail + application logs.
- Focused backend tests (37): pipeline behaviour with a mocked LLM, quote
  verification, LLM-call logging, API contracts, documents, health.
- CI (pytest, frontend typecheck/build, Docker build) and CD (image publish to
  GHCR) via GitHub Actions.
- Single-container deployment (FastAPI serves the built SPA + API), Docker
  Compose with a persistent data volume, migrations applied on boot.

## Intentionally excluded (and why)

- **Authentication / multi-tenancy** — demo scope; deploy behind a reverse
  proxy or add auth before exposing to untrusted networks.
- **Production database (Postgres)** — SQLite keeps the demo zero-ops; the
  SQLAlchemy layer would port cleanly.
- **RAG/vector retrieval** — lexical candidate scoring + LLM judgment proved
  sufficient for the guideline/application lengths in scope; embeddings would
  add infra cost without a measured quality win here.
- **Streaming pipeline progress over WebSocket** — 2s polling is adequate for
  the pipeline durations in scope.
- **File storage on object storage (S3)** — local volume is enough for a
  single-instance deployment.
- **End-to-end browser tests in CI** — `backend/e2e_ui_test.py` and
  `backend/live_api_test.py` exist for manual runs against a live server;
  they need a real LLM key so they stay out of CI.

## Tests

```bash
cd backend
python -m pytest tests -q
```

What the suite covers (all with a mocked LLM — no key or network needed):

- `test_pipeline.py` — end-to-end pipeline: requirement extraction with
  verified and hallucinated quotes, mapping + citation verification, gap
  analysis, claims, questions, completion scoring, re-run carry-forward.
- `test_llm_logs.py` — every LLM call is logged (success, validation-failure
  + repair retry, repeated failure, transport error) and the `/llm-logs`
  endpoint returns rows in order.
- `test_assessment_api.py` — assessment lifecycle, review actions, audit
  trail, 404s and validation errors.
- `test_documents.py` — upload/paste, versioning, size limits, bad files.
- `test_deterministic.py` — quote verification and completion/staleness logic.
- `test_health.py` — health endpoint.

Frontend has no separate test runner; `npm run build` (tsc `--noEmit` + vite)
is the gate and runs in CI.

## Limitations

- LLM output quality depends on the configured model; the verbatim-quote
  checks catch hallucinated citations but cannot judge semantic correctness of
  a real quote taken out of context.
- Supporting-document "evidence" is metadata (name/required/present) —
  document *contents* are not parsed for claim verification.
- SQLite + background tasks assume a single instance; do not scale replicas
  against one database file.
- PDF extraction covers text-based PDFs; scanned/image PDFs will extract
  little or nothing (a warning is surfaced in the UI).
- No authn/z — anyone with network access can read and review.

## Deployment

The app ships as a single image: FastAPI serves the built SPA and the API on
one port, migrations run on boot, and all mutable state lives under `/data`.

### Run with Docker Compose (recommended)

```bash
# provide the LLM key via a local .env next to docker-compose.yml (git-ignored)
# optionally set LLM_BASE_URL to point at an OpenAI-compatible endpoint
echo 'LLM_API_KEY=sk-...' > .env

docker compose up --build -d
# → http://localhost:8000  (UI + API; interactive docs at /docs)
```

Data persists in the `gaca-data` volume (`docker compose down -v` wipes it).

### Run the image directly

```bash
docker build -t gaca .
docker run -d -p 8000:8000 \
  -e LLM_API_KEY=sk-... \
  -e LLM_BASE_URL=https://api.example.com/v1 \
  -v gaca-data:/data \
  gaca
```

### CI/CD

- **CI** (`.github/workflows/ci.yml`) — on push/PR: backend pytest, frontend
  typecheck + build, Docker image build (no push).
- **CD** (`.github/workflows/cd.yml`) — on pushes to `main` and `v*` tags:
  build and publish the image to GitHub Container Registry
  (`ghcr.io/<owner>/<repo>`). Any Docker host can then deploy with
  `docker pull` + the `docker run`/compose recipe above.

### Hosted-demo notes for reviewers

- No account is required; the app has no authentication (see Limitations).
- To exercise the AI pipeline, a valid `LLM_API_KEY` must be set in the
  server environment. Without one, create assessments with **"Run AI
  review"** unchecked — the full manual-review workflow still works.
- Sample inputs: paste any short grant guideline as a "guideline" document
  and a draft application as an "application" document on the Documents
  page, then create an assessment from the New Assessment page.
