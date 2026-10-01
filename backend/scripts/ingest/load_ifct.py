"""IFCT 2017 (ICMR-NIN) → `foods` + `nutrients` (primary nutrition dataset).

Input: the digitized IFCT 2017 composition table (data/raw/ifct2017/compositions.csv, from
scripts/ingest/fetch_raw.py). In that file every nutrient amount is in grams per 100 g edible
portion, except energy, which is in kJ. Blank cells were written as 0, so a zero energy value
means "not analysed" and is skipped.

Usage (from backend/):  uv run python -m scripts.ingest.load_ifct [path/to/compositions.csv]
"""

import csv
import re
import sys
from pathlib import Path

import asyncpg

from scripts.ingest.common import IFCT_CSV, IngestError, dataset_version, run
from scripts.ingest.foods import FoodRecord, replace_dataset

DATASET = "IFCT2017"

KJ_PER_KCAL = 4.184
# canonical nutrient → (IFCT column, factor from the file's unit to the canonical unit)
COLUMNS: dict[str, tuple[str, float]] = {
    "energy_kcal": ("enerc", 1 / KJ_PER_KCAL),
    "protein": ("protcnt", 1),
    "fat": ("fatce", 1),
    "saturated_fat": ("fasat", 1),
    "carbohydrate": ("choavldf", 1),
    "fiber": ("fibtg", 1),
    "sugars": ("fsugar", 1),
    "cholesterol": ("cholc", 1e3),
    "calcium": ("ca", 1e3),
    "iron": ("fe", 1e3),
    "magnesium": ("mg", 1e3),
    "phosphorus": ("p", 1e3),
    "potassium": ("k", 1e3),
    "sodium": ("na", 1e3),
    "zinc": ("zn", 1e3),
    "vitamin_c": ("vitc", 1e3),
    "thiamine": ("thia", 1e3),
    "riboflavin": ("ribf", 1e3),
    "niacin": ("nia", 1e3),
    "vitamin_b6": ("vitb6c", 1e3),
    "folate": ("folsum", 1e6),
    "retinol": ("retol", 1e6),
    "beta_carotene": ("cartb", 1e6),
}
# Values that are 0 only because the cell was blank.
ZERO_MEANS_MISSING = {"energy_kcal"}

# IFCT food group letter → diet. A to L and T are vegetarian, M is egg, N to S are meat and fish.
_EGG_GROUPS = {"M"}
_NONVEG_GROUPS = set("NOPQRS")
# Groups left out of "foods high in X" lists: spices, sugars, misc (toddy, coconut water), oils.
_NOT_RECOMMENDABLE_GROUPS = set("GIKT")
_COOKED_WORDS = re.compile(r"\b(boiled|omlet|omelette|cooked|fried|roasted)\b", re.IGNORECASE)


def diet_for(code: str) -> str:
    group = code[:1]
    if group in _EGG_GROUPS:
        return "egg"
    if group in _NONVEG_GROUPS:
        return "nonveg"
    return "veg"


def parse_local_names(lang: str) -> list[str]:
    """ "A. Chira, Chiura; B., H. Poha; Kan. ?. Avalakki" → ["chira", "chiura", "poha"].

    Each `;`-separated part is one or more language abbreviations followed by names. Unknown
    names are written as "?." in the source and are skipped.
    """
    names: list[str] = []
    for part in lang.split(";"):
        # The list's last name ends with "." ("Tam. Aval."): drop it first, so a one-word name
        # isn't mistaken for a language abbreviation.
        text = re.sub(r"^\s*(?:[A-Z][A-Za-z]*\.\s*,?\s*)+", "", part.strip().rstrip("."))
        for name in re.split(r"[,/]", text):
            name = " ".join(name.split()).lower()
            if len(name) >= 3 and "?" not in name and name not in names:
                names.append(name)
    return names


def parse_ifct(path: Path) -> list[FoodRecord]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise IngestError(f"{path} has no rows")
    missing = {"code", "name", "lang", "grup"} | {col for col, _ in COLUMNS.values()}
    missing -= set(rows[0])
    if missing:
        raise IngestError(f"{path} is missing columns {sorted(missing)}")

    records = []
    for row in rows:
        code = row["code"].strip()
        nutrients: dict[str, float] = {}
        for name, (column, factor) in COLUMNS.items():
            raw = row[column].strip()
            if not raw:
                continue
            amount = float(raw) * factor
            if amount < 0:
                raise IngestError(f"{code}: negative {column} value {raw}")
            if amount == 0 and name in ZERO_MEANS_MISSING:
                continue
            nutrients[name] = round(amount, 4)
        records.append(
            FoodRecord(
                id=f"ifct:{code}",
                name=row["name"].strip(),
                food_group=row["grup"].strip(),
                state="cooked" if _COOKED_WORDS.search(row["name"]) else "raw",
                dataset=DATASET,
                diet=diet_for(code),
                recommendable=code[:1] not in _NOT_RECOMMENDABLE_GROUPS,
                nutrients=nutrients,
                local_names=parse_local_names(row["lang"]),
            )
        )
    return records


async def load_ifct(conn: asyncpg.Connection, path: Path = IFCT_CSV) -> str:
    if not path.exists():
        raise IngestError(f"{path} not found; run scripts/ingest/fetch_raw.py first")
    return await replace_dataset(conn, DATASET, parse_ifct(path), dataset_version(DATASET, path))


if __name__ == "__main__":
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else IFCT_CSV
    run(lambda conn: load_ifct(conn, csv_path))
