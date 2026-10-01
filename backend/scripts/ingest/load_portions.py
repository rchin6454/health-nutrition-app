"""data/portions.yaml → `portion_weights` (Indian household measures).

Run after the food loaders: food-specific entries for foods that are not loaded are skipped
with a warning.

Usage (from backend/):  uv run python -m scripts.ingest.load_portions
"""

from pathlib import Path
from typing import Any

import asyncpg

from app.knowledge.units import normalize_unit
from scripts.ingest.common import PORTIONS_YAML, IngestError, dataset_version, load_yaml, run, warn

PortionRow = tuple[str | None, str, float, str | None]  # food_id, measure, grams, note


def _entry(food_id: str | None, measure: str, spec: Any) -> PortionRow:
    canonical = normalize_unit(measure)
    if canonical != measure:
        raise IngestError(f"{food_id or 'generic'}: use the measure {canonical!r}, not {measure!r}")
    grams = float(spec["grams"])
    if grams <= 0:
        raise IngestError(f"{food_id or 'generic'} {measure}: grams must be positive")
    return (food_id, measure, grams, spec.get("note"))


def parse_portions(path: Path = PORTIONS_YAML) -> list[PortionRow]:
    data: Any = load_yaml(path)
    rows = [_entry(None, measure, spec) for measure, spec in data["generic"].items()]
    for food_id, measures in data["foods"].items():
        rows.extend(_entry(food_id, measure, spec) for measure, spec in measures.items())
    return rows


async def load_portions(conn: asyncpg.Connection, path: Path = PORTIONS_YAML) -> str:
    rows = parse_portions(path)
    known = {r["id"] for r in await conn.fetch("SELECT id FROM foods")}
    kept = []
    for row in rows:
        if row[0] is not None and row[0] not in known:
            warn(f"portions: skipping {row[0]} {row[1]} (food not loaded)")
        else:
            kept.append(row)
    version = dataset_version("portions", path)
    async with conn.transaction():
        await conn.execute("DELETE FROM portion_weights")
        await conn.executemany(
            """
            INSERT INTO portion_weights (food_id, measure, grams, note, dataset_version)
            VALUES ($1, $2, $3, $4, $5)
            """,
            [(*row, version) for row in kept],
        )
    return f"portions {version}: {len(kept)} measures ({len(rows) - len(kept)} skipped)"


if __name__ == "__main__":
    run(load_portions)
