FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.14 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 \
    EMBEDDING_CACHE_DIR=/app/models

# Install dependencies first so this layer is cached between code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH"

# Download the embedding model at build time, so startup never waits on the network.
RUN python -c "import os; from fastembed import TextEmbedding; \
TextEmbedding(os.environ['EMBEDDING_MODEL'], cache_dir=os.environ['EMBEDDING_CACHE_DIR'])"

COPY app ./app

# Railway injects $PORT; default to 8000 for local runs.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
