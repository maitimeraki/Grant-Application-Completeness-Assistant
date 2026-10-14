# Agent Usage Log

This document records how AI agents were used to build this project, what was
delegated, what went wrong, and how outputs were verified. It is intended for
reviewers evaluating AI-assisted development of this repository.

## Tools used

| Tool | Purpose |
|---|---|
| OpenCode agent (Mimo v2.6) | Primary implementation agent: code exploration, writing backend/frontend/infra, documentation |
| Explore subagent | Fast codebase reconnaissance (file patterns, API surface, existing conventions) |
| Read / Grep / Glob | Manual file inspection to ground every change in existing code style |
| pytest (backend venv) | Backend test execution and verification |
| npm / tsc / vite | Frontend typecheck and production build as a correctness gate |
| PowerShell | Local environment operations (venv, alembic migrations, test runs) |

## Representative prompts

The user's requirements arrived as a rubric (usable frontend + backend +
persistence; LLM workflow with human review; loading/empty/validation/success/
failure states; structured logs + focused tests; deployment; README,
AGENT_USAGE, .env.example; no secrets committed). Representative prompts given
to the agent during this project:

1. *"this is the requirement as it integrated and what is not"* — assess the
   existing codebase against the rubric and report integrated vs missing.
   The agent explored the repo, ran the existing test suite, and produced a
   gap table (missing: deployment, README, AGENT_USAGE, structured AI logs,
   success feedback; present: frontend, backend, persistence, LLM pipeline,
   human review, tests).
2. *"no git means do not push code into git by yourself, instead you can do
   others"* — close every gap except version-control operations.
3. *"no git means do not push code into git by yourself, but create ci/cd
   and all"* — clarified scope: the agent must not run any `git` command, but
   CI/CD workflow files should be created.

## Delegated work

Delegated to subagents in this session:

- **Codebase reconnaissance** — locating all backend services, routers,
  models, frontend pages, and tests before making changes (kept the main
  agent's context focused on edit sites).

Everything else (design and implementation of the LLM-call logging, review UI
polish, Docker packaging, CI/CD workflows, documentation) was done directly
by the main agent.

## Important agent mistakes and rejected suggestions

- **Suggested `git init` + push to GitHub** as the first remediation step.
  The user rejected this: the agent must never run git commands on this
  machine. Corrected approach: create CI/CD *files* only; the user performs
  version control themselves.
- **First Dockerfile had two path/order bugs**: `COPY backend/ ./backend/`
  would have nested the app incorrectly under `WORKDIR /app/backend`, and a
  `chown` to `appuser` ran before the user was created (build failure). Both
  were caught by re-reading the Dockerfile before any build attempt and
  fixed prior to verification.
- **Test-ordering flake**: the first version of the `/llm-logs` endpoint test
  inserted two rows in one flush; identical `created_at` timestamps made the
  assertion order-dependent. Fixed by inserting explicit, distinct
  timestamps so the test asserts behaviour, not flush luck.
- **First pytest run timed out at 120 s** — not a product bug: the initial
  import of `litellm` is slow (~90 s cold). Subsequent runs used a longer
  timeout; this is documented so future contributors don't chase a phantom
  hang.
- **Assumed `rtk` CLI was available** (per a global user config); the command
  does not exist on this machine. Fell back to plain PowerShell and the
  dedicated Read/Grep/Glob tools instead.

## How output was verified

| Change | Verification |
|---|---|
| LLM-call logging (model, migration, `llm.py`, pipeline stage tags) | New `tests/test_llm_logs.py`: 6 tests covering success, validation-failure + repair retry, repeated failure, transport error, endpoint ordering, 404. All pass. |
| `/api/assessments/{id}/llm-logs` endpoint | Same test file (ordered rows, error propagation, 404); exercised through FastAPI's TestClient. |
| Alembic migration `c3f8a2d91b47` | `alembic upgrade head` applied cleanly to the dev database. |
| Full backend regression | `python -m pytest tests -q` → **37 passed** (31 pre-existing + 6 new). |
| Frontend changes (toasts, AI-run-log section, types) | `npm run build` = `tsc --noEmit` (strict typecheck) + vite production bundle → clean. |
| Dockerfile / compose / entrypoint | Re-read after writing (caught the two bugs above); image build is additionally gated by the `docker-build` job in CI. |
| CI/CD workflows | YAML reviewed against current GitHub Actions versions (`actions/checkout@v4`, `docker/build-push-action@v6`, etc.); activated once the repo exists on GitHub. |
| Secrets hygiene | `backend/.env` is git-ignored and never written by the agent; `.env.example` contains names only; `.dockerignore` excludes `.env` and data dirs from image context. |
