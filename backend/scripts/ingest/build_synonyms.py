"""Food names → `food_synonyms` (English + Hindi + regional names).

Three sources, in order of preference when the resolver finds several matches:
  1. curated   data/synonyms.yaml (settles ambiguity: "rice" → raw milled rice)
  2. dataset   each loaded food's own name ("Rice, raw, brown" → "rice raw brown")
  3. local     IFCT's local-name column (Hindi, Tamil, Bengali, … names)
Derived names (2, 3) that would point to more than one food, or that a curated entry already
uses, are dropped. Run after the food loaders.

Usage (from backend/):  uv run python -m scripts.ingest.build_synonyms [path/to/compositions.csv]
"""

import sys
from pathlib import Path
from typing import Any

import asyncpg

from app.knowledge.entity_resolver import normalize_name
from scripts.ingest.common import (
    IFCT_CSV,
    SYNONYMS_YAML,
    IngestError,
    dataset_version,
    load_yaml,
    run,
    warn,
)
from scripts.ingest.load_ifct import parse_ifct

SynonymRow = tuple[str, str, str]  # synonym, food_id, origin


def parse_curated(path: Path = SYNONYMS_YAML) -> dict[str, str]:
    """Normalized synonym → food ID. A name listed under two foods is an error."""
    data: Any = load_yaml(path)
    curated: dict[str, str] = {}
    for food_id, names in data["synonyms"].items():
        for name in names:
            synonym = normalize_name(str(name))
            if synonym in curated and curated[synonym] != food_id:
                raise IngestError(f"{name!r} is listed under {curated[synonym]} and {food_id}")
            curated[synonym] = food_id
    return curated


def build_rows(
    curated: dict[str, str],
    food_names: dict[str, str],
    local_names: dict[str, list[str]],
) -> list[SynonymRow]:
    """All synonym rows for the loaded foods (`food_names`: food ID → name)."""
    rows: list[SynonymRow] = []
    for synonym, food_id in curated.items():
        if food_id in food_names:
            rows.append((synonym, food_id, "curated"))
        else:
            warn(f"synonyms: skipping {synonym!r} → {food_id} (food not loaded)")

    for origin, names_by_food in [
        ("dataset_name", {food_id: [name] for food_id, name in food_names.items()}),
        ("local_name", local_names),
    ]:
        foods_by_name: dict[str, set[str]] = {}
        for food_id, names in names_by_food.items():
            if food_id not in food_names:
                continue
            for name in names:
                synonym = normalize_name(name)
                if synonym:
                    foods_by_name.setdefault(synonym, set()).add(food_id)
        taken = {row[0] for row in rows}
        for synonym, food_ids in sorted(foods_by_name.items()):
            if len(food_ids) == 1 and synonym not in taken:
                rows.append((synonym, food_ids.pop(), origin))
    return rows


async def build_synonyms(
    conn: asyncpg.Connection, ifct_csv: Path = IFCT_CSV, curated_path: Path = SYNONYMS_YAML
) -> str:
    curated = parse_curated(curated_path)
    food_names = {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM foods")}
    local_names: dict[str, list[str]] = {}
    inputs = [curated_path]
    if ifct_csv.exists():
        local_names = {r.id: r.local_names for r in parse_ifct(ifct_csv)}
        inputs.append(ifct_csv)
    else:
        warn(f"{ifct_csv} not found; IFCT local names are not added")
    rows = build_rows(curated, food_names, local_names)
    version = dataset_version("synonyms", *inputs)
    async with conn.transaction():
        await conn.execute("DELETE FROM food_synonyms")
        await conn.executemany(
            """
            INSERT INTO food_synonyms (synonym, food_id, origin, dataset_version)
            VALUES ($1, $2, $3, $4)
            """,
            [(*row, version) for row in rows],
        )
    origins = ("curated", "dataset_name", "local_name")
    counts = {origin: sum(1 for r in rows if r[2] == origin) for origin in origins}
    return f"synonyms {version}: {len(rows)} names {counts}"


if __name__ == "__main__":
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else IFCT_CSV
    run(lambda conn: build_synonyms(conn, csv_path))
