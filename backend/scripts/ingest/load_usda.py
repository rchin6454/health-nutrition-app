"""USDA FoodData Central → `foods` + `nutrients` (fallback for foods IFCT does not cover).

Only the FDC IDs listed in data/usda_foods.yaml are loaded. Input: FoodData Central CSV
downloads unzipped under data/raw/usda/ (SR Legacy and FNDDS survey foods; any FDC CSV folder
works). Nutrients are matched by their stable nutrient number ("203" = protein). In SR Legacy
and Foundation files `food_nutrient.nutrient_id` is the nutrient's id; in FNDDS survey files it
is the nutrient number itself.

Usage (from backend/):  uv run python -m scripts.ingest.load_usda [path/to/usda_dir]
"""

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

from app.knowledge.nutrients import NUTRIENTS
from scripts.ingest.common import (
    USDA_DIR,
    USDA_FOODS_YAML,
    IngestError,
    dataset_version,
    load_yaml,
    run,
)
from scripts.ingest.foods import FoodRecord, replace_dataset

DATASET = "USDA_FDC"

# canonical nutrient → USDA nutrient numbers, in order of preference
NUTRIENT_NUMBERS: dict[str, tuple[str, ...]] = {
    "energy_kcal": ("208", "958", "957"),  # Energy; Atwater specific; Atwater general
    "protein": ("203",),
    "fat": ("204",),
    "saturated_fat": ("606",),
    "carbohydrate": ("205",),
    "fiber": ("291",),
    "sugars": ("269",),
    "cholesterol": ("601",),
    "calcium": ("301",),
    "iron": ("303",),
    "magnesium": ("304",),
    "phosphorus": ("305",),
    "potassium": ("306",),
    "sodium": ("307",),
    "zinc": ("309",),
    "vitamin_c": ("401",),
    "thiamine": ("404",),
    "riboflavin": ("405",),
    "niacin": ("406",),
    "vitamin_b6": ("415",),
    "folate": ("417",),
    "retinol": ("319",),
    "beta_carotene": ("321",),
}
_USDA_UNITS = {"G": "g", "MG": "mg", "UG": "µg", "KCAL": "kcal"}


@dataclass(frozen=True)
class UsdaFood:
    fdc_id: str
    name: str
    state: str | None
    diet: str
    food_group: str


def read_food_list(path: Path = USDA_FOODS_YAML) -> list[UsdaFood]:
    data: Any = load_yaml(path)
    foods = [
        UsdaFood(
            fdc_id=str(entry["fdc_id"]),
            name=entry["name"],
            state=entry["state"],
            diet=entry["diet"],
            food_group=entry["food_group"],
        )
        for entry in data["foods"]
    ]
    ids = [f.fdc_id for f in foods]
    if len(ids) != len(set(ids)):
        raise IngestError(f"{path} lists an FDC ID more than once")
    return foods


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _nutrient_numbers(folder: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(food_nutrient.nutrient_id value → nutrient number, nutrient number → unit)."""
    rows = _read_csv(folder / "nutrient.csv")
    units = {r["nutrient_nbr"]: r["unit_name"] for r in rows if r["nutrient_nbr"]}
    if (folder / "survey_fndds_food.csv").exists():
        return {nbr: nbr for nbr in units}, units
    return {r["id"]: r["nutrient_nbr"] for r in rows if r["nutrient_nbr"]}, units


def parse_usda_folder(folder: Path, wanted: set[str]) -> dict[str, dict[str, float]]:
    """Per-100 g amounts by nutrient number for each wanted FDC ID found in one CSV folder."""
    found = {r["fdc_id"] for r in _read_csv(folder / "food.csv") if r["fdc_id"] in wanted}
    if not found:
        return {}
    to_number, units = _nutrient_numbers(folder)
    amounts: dict[str, dict[str, float]] = {fdc_id: {} for fdc_id in found}
    with (folder / "food_nutrient.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            fdc_id = row["fdc_id"]
            if fdc_id not in found or not row["amount"]:
                continue
            number = to_number.get(row["nutrient_id"])
            if number:
                amounts[fdc_id][number] = float(row["amount"])
    for name, numbers in NUTRIENT_NUMBERS.items():
        for number in numbers:
            unit = _USDA_UNITS.get(units.get(number, ""))
            if number in units and unit != NUTRIENTS[name].unit:
                raise IngestError(f"{folder.name}: nutrient {number} is in {units[number]}")
    return amounts


def standardize(amounts: dict[str, float]) -> dict[str, float]:
    """USDA nutrient numbers → canonical names (first available number wins)."""
    nutrients: dict[str, float] = {}
    for name, numbers in NUTRIENT_NUMBERS.items():
        for number in numbers:
            if number in amounts:
                nutrients[name] = round(amounts[number], 4)
                break
    return nutrients


def parse_usda(usda_dir: Path, foods: list[UsdaFood]) -> list[FoodRecord]:
    wanted = {f.fdc_id for f in foods}
    amounts: dict[str, dict[str, float]] = {}
    for food_csv in sorted(usda_dir.glob("*/food.csv")):
        amounts.update(parse_usda_folder(food_csv.parent, wanted - set(amounts)))
    missing = sorted(wanted - set(amounts))
    if missing:
        raise IngestError(f"FDC IDs not found under {usda_dir}: {', '.join(missing)}")
    records = []
    for food in foods:
        nutrients = standardize(amounts[food.fdc_id])
        if "energy_kcal" not in nutrients:
            raise IngestError(f"FDC {food.fdc_id} ({food.name}) has no energy value")
        records.append(
            FoodRecord(
                id=f"usda:{food.fdc_id}",
                name=food.name,
                food_group=food.food_group,
                state=food.state,
                dataset=DATASET,
                diet=food.diet,
                recommendable=False,  # recommendation lists use IFCT foods only
                nutrients=nutrients,
            )
        )
    return records


async def load_usda(
    conn: asyncpg.Connection, usda_dir: Path = USDA_DIR, food_list: Path = USDA_FOODS_YAML
) -> str:
    if not usda_dir.exists():
        raise IngestError(f"{usda_dir} not found; run scripts/ingest/fetch_raw.py first")
    records = parse_usda(usda_dir, read_food_list(food_list))
    version = dataset_version(DATASET, food_list, *sorted(usda_dir.glob("*/food.csv")))
    return await replace_dataset(conn, DATASET, records, version)


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else USDA_DIR
    run(lambda conn: load_usda(conn, folder))
