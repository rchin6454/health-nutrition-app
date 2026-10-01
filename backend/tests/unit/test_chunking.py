"""Guidance documents → chunks (scripts/ingest/chunk_and_embed.py) and the pgvector format."""

from pathlib import Path

import pytest

from app.knowledge.embeddings import EMBEDDING_DIM, to_pgvector
from scripts.ingest.chunk_and_embed import (
    CHUNK_TOKENS,
    WORDS_PER_TOKEN,
    chunk_doc,
    guidance_files,
    split_words,
)
from scripts.ingest.common import GUIDANCE_DIR, IngestError

ALL_CHUNKS = [c for path in guidance_files(GUIDANCE_DIR) for c in chunk_doc(path)]


def test_short_text_is_one_chunk() -> None:
    assert split_words("a b  c\nd", 500, 50) == ["a b c d"]


def test_long_text_is_split_into_overlapping_windows() -> None:
    words = [f"w{i}" for i in range(100)]
    windows = split_words(" ".join(words), max_tokens=40, overlap_tokens=8)  # 30 words, 6 overlap
    assert [len(w.split()) for w in windows] == [30, 30, 30, 28]
    assert windows[0].split()[-6:] == windows[1].split()[:6]
    assert windows[-1].split()[-1] == "w99"


def test_every_guidance_document_is_chunked_by_section() -> None:
    docs = {c.doc for c in ALL_CHUNKS}
    assert len(docs) == len(guidance_files(GUIDANCE_DIR))
    assert {c.category for c in ALL_CHUNKS} == {"nutrition", "food_safety"}
    rice = [c for c in ALL_CHUNKS if "Leftover rice and the heat" in c.text]
    assert len(rice) == 1
    assert rice[0].text.startswith(
        "Food safety in Indian homes and markets — Leftover rice and the heat: Cooked rice"
    )


def test_chunks_stay_under_the_token_budget() -> None:
    assert all(len(c.text.split()) <= CHUNK_TOKENS * WORDS_PER_TOKEN + 20 for c in ALL_CHUNKS)


@pytest.mark.parametrize("authority", ["WHO", "USDA", "FSIS", "ICMR", "NIN"])
def test_passage_text_never_cites_an_authority(authority: str) -> None:
    # Passages reach the prompt; the source stays in `origin` for audit only.
    for chunk in ALL_CHUNKS:
        assert f"{authority} " not in f"{chunk.text} ", chunk.text[:60]
        assert chunk.origin


def test_documents_without_front_matter_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("## Heading\n\nText.\n")
    with pytest.raises(IngestError, match="front matter"):
        chunk_doc(path)


def test_documents_need_a_known_category(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("---\ntitle: T\ncategory: recipes\norigin: O\n---\n\n## H\n\nText.\n")
    with pytest.raises(IngestError, match="category"):
        chunk_doc(path)


def test_pgvector_literal() -> None:
    vector = [0.5] + [0.0] * (EMBEDDING_DIM - 1)
    literal = to_pgvector(vector)
    assert literal.startswith("[0.5,0,0,")
    assert literal.endswith(",0]")
    with pytest.raises(ValueError, match="384"):
        to_pgvector([0.1, 0.2])
