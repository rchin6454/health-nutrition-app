"""The food record both dataset loaders produce, and its idempotent write to the database."""

from dataclasses import dataclass, field

import asyncpg

from app.knowledge.nutrients import NUTRIENTS


@dataclass
class FoodRecord:
    id: str  # "ifct:A015", "usda:169757"
    name: str
    food_group: str
    state: str | None  # raw | cooked
    dataset: str  # IFCT2017 | USDA_FDC
    diet: str  # veg | egg | nonveg
    recommendable: bool
    nutrients: dict[str, float] = field(default_factory=dict)  # canonical → per 100 g
    local_names: list[str] = field(default_factory=list)  # IFCT only, used for synonyms


async def replace_dataset(
    conn: asyncpg.Connection, dataset: str, records: list[FoodRecord], version: str
) -> str:
    """Upsert `records` as the whole of `dataset`: foods missing from them are deleted.

    Foods are upserted rather than deleted and re-inserted, so synonyms and portion weights that
    point to them survive a reload.
    """
    for record in records:
        unknown = set(record.nutrients) - set(NUTRIENTS)
        if unknown:
            raise ValueError(f"{record.id}: unknown nutrients {sorted(unknown)}")
    ids = [r.id for r in records]
    async with conn.transaction():
        await conn.executemany(
            """
            INSERT INTO foods (id, name, food_group, state, dataset, diet, recommendable,
                               dataset_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, food_group = EXCLUDED.food_group, state = EXCLUDED.state,
                dataset = EXCLUDED.dataset, diet = EXCLUDED.diet,
                recommendable = EXCLUDED.recommendable, dataset_version = EXCLUDED.dataset_version
            """,
            [
                (r.id, r.name, r.food_group, r.state, dataset, r.diet, r.recommendable, version)
                for r in records
            ],
        )
        removed = await conn.fetchval(
            """
            WITH gone AS (
                DELETE FROM foods WHERE dataset = $1 AND NOT (id = ANY($2::text[])) RETURNING 1
            ) SELECT count(*) FROM gone
            """,
            dataset,
            ids,
        )
        await conn.execute("DELETE FROM nutrients WHERE food_id = ANY($1::text[])", ids)
        await conn.executemany(
            "INSERT INTO nutrients (food_id, nutrient, amount, unit) VALUES ($1, $2, $3, $4)",
            [
                (r.id, name, amount, NUTRIENTS[name].unit)
                for r in records
                for name, amount in r.nutrients.items()
            ],
        )
    values = sum(len(r.nutrients) for r in records)
    return (
        f"{dataset} {version}: {len(records)} foods, {values} nutrient values"
        f" ({removed} stale foods removed)"
    )
