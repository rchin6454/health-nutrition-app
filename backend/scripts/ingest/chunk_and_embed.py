"""data/guidance/*.md → ~500-token chunks → fastembed → `doc_chunks`.

Each guidance document has YAML front matter (`title`, `category`, `origin`) and `## ` sections.
A chunk never crosses a section boundary, so each passage stays on one topic. Sections longer
than ~500 tokens are split into overlapping windows of 500 tokens with a 50-token overlap. Each
chunk starts with "<title> — <section>" so it reads on its own. `origin` is stored for audit
only and never reaches the prompt.

Tokens are approximated from words (1 token ≈ 0.75 words for English prose), which is
close enough for chunk sizing.

Usage (from backend/):  uv run python -m scripts.ingest.chunk_and_embed
The first run downloads the embedding model (~70 MB) into the fastembed cache.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from app.config import get_settings
from app.knowledge.embeddings import EMBEDDING_DIM, Embedder, FastEmbedder, to_pgvector
from scripts.ingest.common import GUIDANCE_DIR, IngestError, dataset_version, run

CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50
WORDS_PER_TOKEN = 0.75
CATEGORIES = {"nutrition", "food_safety"}

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Chunk:
    category: str
    doc: str
    text: str
    origin: str


def split_words(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Overlapping windows of at most `max_tokens` (approximated from words)."""
    words = text.split()
    size = int(max_tokens * WORDS_PER_TOKEN)
    step = size - int(overlap_tokens * WORDS_PER_TOKEN)
    if len(words) <= size:
        return [" ".join(words)]
    windows = []
    for start in range(0, len(words), step):
        windows.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
    return windows


def parse_doc(path: Path) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Front matter, and (heading, body) for each `## ` section."""
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        raise IngestError(f"{path.name}: missing front matter")
    meta: dict[str, Any] = yaml.safe_load(match.group(1))
    for key in ("title", "category", "origin"):
        if not meta.get(key):
            raise IngestError(f"{path.name}: front matter needs {key!r}")
    if meta["category"] not in CATEGORIES:
        raise IngestError(f"{path.name}: unknown category {meta['category']!r}")
    sections = []
    for block in re.split(r"^## ", match.group(2), flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition("\n")
        body = " ".join(body.split())
        if body:
            sections.append((heading.strip(), body))
    if not sections:
        raise IngestError(f"{path.name}: no '## ' sections")
    return meta, sections


def chunk_doc(
    path: Path, max_tokens: int = CHUNK_TOKENS, overlap_tokens: int = OVERLAP_TOKENS
) -> list[Chunk]:
    meta, sections = parse_doc(path)
    origin = " ".join(str(meta["origin"]).split())
    chunks = []
    for heading, body in sections:
        for window in split_words(body, max_tokens, overlap_tokens):
            text = f"{meta['title']} — {heading}: {window}"
            chunks.append(Chunk(meta["category"], path.name, text, origin))
    return chunks


def guidance_files(docs_dir: Path) -> list[Path]:
    files = sorted(docs_dir.glob("*.md"))
    if not files:
        raise IngestError(f"no guidance documents in {docs_dir}")
    return files


async def chunk_and_embed(
    conn: asyncpg.Connection,
    docs_dir: Path = GUIDANCE_DIR,
    embedder: Embedder | None = None,
) -> str:
    files = guidance_files(docs_dir)
    chunks = [c for path in files for c in chunk_doc(path)]
    if embedder is None:
        settings = get_settings()
        embedder = FastEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    vectors = embedder.embed_passages([c.text for c in chunks])
    if any(len(v) != EMBEDDING_DIM for v in vectors):
        raise IngestError(f"the embedding model must return {EMBEDDING_DIM} dimensions")
    version = dataset_version("guidance", *files)
    async with conn.transaction():
        await conn.execute("DELETE FROM doc_chunks")
        await conn.executemany(
            """
            INSERT INTO doc_chunks (category, doc, text, origin, embedding, dataset_version)
            VALUES ($1, $2, $3, $4, $5::vector, $6)
            """,
            [
                (c.category, c.doc, c.text, c.origin, to_pgvector(v), version)
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )
    return f"guidance {version}: {len(chunks)} chunks from {len(files)} documents"


if __name__ == "__main__":
    run(chunk_and_embed)
