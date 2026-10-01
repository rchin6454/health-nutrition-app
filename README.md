# Food, Nutrition & Food Safety Chatbot

An India-first chatbot that answers questions about food, nutrition and food safety.

- What it must do: [problemStatement.md](problemStatement.md)
- How it is designed: [architecture.md](architecture.md)
- Build plan and progress: [implementation-plan.md](implementation-plan.md)

## Repository layout

| Folder | What it is | Deployed to |
|--------|------------|-------------|
| `backend/` | FastAPI (Python 3.12, managed with `uv`) — the only place model calls happen | Railway |
| `frontend/` | Next.js 16 + TypeScript + Tailwind chat UI | Vercel |

## Local setup

Prerequisites: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Node.js 24+.

```bash
# environment variables
cp .env.example backend/.env          # fill in GROQ_API_KEY, DATABASE_URL, ...
cp .env.example frontend/.env.local   # only NEXT_PUBLIC_API_URL is used here

# backend
cd backend
uv sync
uv run python scripts/migrate.py                    # apply migrations/*.sql to DATABASE_URL
uv run uvicorn app.main:app --reload --port 8000   # http://localhost:8000/api/health

# frontend (in another terminal)
cd frontend
npm install
npm run dev                                         # http://localhost:3000
npm run gen:types                                   # regenerate lib/types.ts after schema changes (backend must be running)

# git hooks (once, from the repo root)
backend/.venv/bin/pre-commit install
```

## Checks (same as CI)

```bash
cd backend  && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run format:check && npm test && npm run build
```

Backend integration tests need Postgres: they use `TEST_DATABASE_URL` if set (CI uses a Postgres
service container), otherwise they start an embedded server via the `pgserver` dev dependency.
They never call Groq or touch the Supabase database.

## Evals

The eval suite (`backend/tests/evals/`, 114 labelled cases in `cases.yaml`) runs each question
through the real pipeline against the real Groq models and checks the result in code: category,
`answer_type`, nutrient numbers within ±5% of the reference data, the food-safety verdict at the
start of the answer, scope blocks made by code (no model call), 100% schema parse and 100%
`source == null`. It needs `GROQ_API_KEY` and a database with the knowledge tables loaded
(`EVAL_DATABASE_URL`, or `DATABASE_URL`); it only reads that database.

```bash
cd backend
uv run python -m tests.evals.run_evals --smoke            # 12 cases, what CI runs
uv run python -m tests.evals.run_evals                    # all cases (paced by Groq limits)
uv run python -m tests.evals.run_evals --slice food_safety --case nut-paneer-protein
uv run python -m tests.evals.run_evals --resume tests/evals/results/<file>.json
uv run python -m tests.evals.run_evals --judge            # add the LLM-as-judge rubric
```

Results go to `tests/evals/results/{date}_{PROMPT_VERSION}.json` (git-ignored); baselines are
recorded in [implementation-plan.md](implementation-plan.md). On the Groq free tier a full run
needs more than one day's token budget: it stops at the daily limit, and `--resume` continues it.
Groq limits are per organization, so evals run with the production key share the live app's
budget. The `Evals` GitHub workflow runs the smoke subset when prompts, schemas, scope gates or
evals change, and any selection on demand (needs the `GROQ_API_KEY` repository secret).

## Reviewing failures

Every failure is a row in `failures` (architecture §8). To read them:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" \
  "https://<backend>/api/admin/failures?severity=error&since=2026-10-01T00:00:00Z&limit=50"
# filters: failure_type, stage, severity, since, until; next page: before_id=<next_before_id>
```

Saved queries for the Supabase SQL editor are in
[backend/sql/failure_review.sql](backend/sql/failure_review.sql): failures per day by type, top
recurring failure types, warning/error rate per prompt version, and the R4 `source` check.

**Weekly review routine**
1. Run "top recurring failure types" for the last 7 days (scope blocks are excluded: they are the
   gates working).
2. For every type seen at least twice, open an issue with its latest `request_id`s, `raw_output`
   and `error_detail`.
3. Fix the root cause in the prompt, schema or code. Never hide it with retries, JSON repair or
   post-processing (R7).
4. Add an eval case that reproduces it (with a `note`), bump `PROMPT_VERSION` if a prompt
   changed, and run the evals before merging.
5. Check "warning rate per prompt version" to confirm the new version is no worse.

## Load test

`backend/tests/load/` runs 20 concurrent users with [locust](https://locust.io/). Against a local
stub server, everything but the model runs for real (gates, rate limits, Groq budgets, knowledge
lookup, storage, failure recording); the stub answers with realistic latency and injects invalid
output, so the test checks that every `error` response has a `failures` row.

```bash
cd backend
DATABASE_URL=<local db with knowledge loaded> uv run --group load python -m tests.load.stub_server
uv run --group load locust -f tests/load/locustfile.py --headless -u 20 -r 5 -t 2m \
  --host http://127.0.0.1:8765                      # pass: p95 < 6 s, every body a ChatResponse
DATABASE_URL=<same db> uv run python -m tests.load.check_failures
```

## Deployment

- **Backend (Railway):** service root directory `backend/`; `railway.toml` builds the Dockerfile and
  health-checks `/api/health`. Set `GROQ_API_KEY`, `DATABASE_URL`, `MODEL_ANSWER`,
  `MODEL_UNDERSTANDING`, `ADMIN_TOKEN` and `ALLOWED_ORIGINS` (the Vercel production URL).
  Optional: `RATE_LIMIT_CHAT` (default `20/minute`) and `TRUSTED_PROXY_HOPS` (default 1, right
  for Railway). Logs are JSON, one object per line.
- **Frontend (Vercel):** project root directory `frontend/`; set `NEXT_PUBLIC_API_URL` to the Railway URL.
- **Database (Supabase):** run `uv run python scripts/migrate.py` with `DATABASE_URL` pointing at the project.
