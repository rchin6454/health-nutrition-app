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
uv run uvicorn app.main:app --reload --port 8000   # http://localhost:8000/api/health

# frontend (in another terminal)
cd frontend
npm install
npm run dev                                         # http://localhost:3000

# git hooks (once, from the repo root)
backend/.venv/bin/pre-commit install
```

## Checks (same as CI)

```bash
cd backend  && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run format:check && npm test && npm run build
```
