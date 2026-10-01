"""Text embeddings for passage retrieval (architecture §5.4, step 5).

`fastembed` with BAAI/bge-small-en-v1.5 (384 dimensions, ONNX, no PyTorch). The model is loaded
once per process at startup (the Dockerfile downloads it at build time) and `/api/health`
reports whether it is ready. Until it is, retrieval raises `EmbeddingsUnavailableError`, which
the pipeline records as a failure before answering without passages.
"""

import logging
from collections.abc import Sequence
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384

EmbeddingStatus = Literal["not_loaded", "ok", "unavailable"]


class Embedder(Protocol):
    # How similar a passage must be to be shown to the model. Both depend on the embedding
    # model's score range, so each embedder carries its own:
    # - min_score: cosine similarity below which a passage is too unrelated;
    # - score_margin: how far below the best match a passage may score and still be kept.
    min_score: float
    score_margin: float

    def embed_query(self, text: str) -> list[float]: ...

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedder:
    # bge-small-en-v1.5 scores are compressed into roughly 0.4-0.9. Measured on the guidance
    # passages: unrelated text scores 0.4-0.55, loosely related food text 0.55-0.65, and a
    # passage on the question's topic 0.7-0.85. Same-domain passages score close together, so
    # a fixed cut-off alone would keep loosely related ones; the margin drops them.
    min_score = 0.65
    score_margin = 0.1

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_query(self, text: str) -> list[float]:
        [vector] = list(self._model.query_embed([text]))
        return [float(x) for x in vector]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._model.passage_embed(list(texts))]


class EmbeddingsUnavailableError(RuntimeError):
    """The embedding model is not loaded (still starting, or it failed to load)."""


_embedder: Embedder | None = None
_status: EmbeddingStatus = "not_loaded"


def load(model_name: str, cache_dir: str | None = None) -> None:
    """Load the model (blocking; run it in a thread). On failure the status is `unavailable`."""
    global _embedder, _status
    try:
        _embedder = FastEmbedder(model_name, cache_dir)
    except Exception:
        _embedder, _status = None, "unavailable"
        raise
    _status = "ok"


def status() -> EmbeddingStatus:
    return _status


def get_embedder() -> Embedder:
    if _embedder is None:
        raise EmbeddingsUnavailableError(f"embedding model is {_status}")
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Install an embedder directly (used by tests and ingestion scripts)."""
    global _embedder, _status
    _embedder = embedder
    _status = "ok" if embedder is not None else "not_loaded"


def to_pgvector(vector: Sequence[float]) -> str:
    """The text form pgvector accepts for a `$n::vector` parameter: "[0.1,0.2,…]"."""
    if len(vector) != EMBEDDING_DIM:
        raise ValueError(f"expected {EMBEDDING_DIM} dimensions, got {len(vector)}")
    return "[" + ",".join(format(x, ".7g") for x in vector) + "]"
