"""Maps the understanding step's food names to food IDs (architecture §5.4, step 1).

Exact synonym match (English, Hindi and regional names) first, then a `rapidfuzz` fallback.
Ties go to curated synonyms, then IFCT over USDA. Names that match nothing are returned as
unresolved so the answer can say the data could not be verified.

The fuzzy fallback is deliberately narrow. Single-word Indian food names that differ by one
letter are often different foods ("kheer"/"kheera", "makhana"/"makhan"), and a wrong match gives
the user wrong numbers, which is worse than "could not verify". So it only runs for multi-word
names (word order, "rice brown" ↔ "brown rice") and only against curated and dataset names, not
the thousands of IFCT local-language names.

The synonym index is small (a few thousand rows) and only changes when the ingestion scripts
run, so it is loaded once per process; restart the backend after re-ingesting.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz, process

from app import db

FUZZY_THRESHOLD = 85
FUZZY_MIN_WORDS = 2

_ORIGIN_RANK = {"curated": 0, "dataset_name": 1, "local_name": 2}
_DATASET_RANK = {"IFCT2017": 0, "USDA_FDC": 1}


@dataclass(frozen=True)
class FoodEntry:
    id: str
    name: str
    dataset: str  # IFCT2017 | USDA_FDC
    state: str | None  # raw | cooked


@dataclass(frozen=True)
class SynonymRow:
    synonym: str
    food: FoodEntry
    origin: str  # curated | dataset_name | local_name


@dataclass(frozen=True)
class ResolvedFood:
    query: str  # the name as the understanding step gave it
    food: FoodEntry
    match: Literal["exact", "fuzzy"]
    score: float  # 100 for exact matches


def normalize_name(name: str) -> str:
    """Lowercase, punctuation to spaces, single spaces: "Rice, raw (brown)" → "rice raw brown"."""
    return " ".join(re.sub(r"[^\w\s]|_", " ", name.lower()).split())


def _singular_variants(name: str) -> list[str]:
    """ "rotis" → "roti", "tomatoes" → "tomato", "berries" → "berry" (last word only)."""
    head, _, last = name.rpartition(" ")
    prefix = f"{head} " if head else ""
    variants = []
    if last.endswith("ies") and len(last) > 4:
        variants.append(prefix + last[:-3] + "y")
    if last.endswith("es") and len(last) > 3:
        variants.append(prefix + last[:-2])
    if last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        variants.append(prefix + last[:-1])
    return variants


def _preference(row: SynonymRow) -> tuple[int, int, str]:
    return (_ORIGIN_RANK.get(row.origin, 9), _DATASET_RANK.get(row.food.dataset, 9), row.food.id)


class SynonymIndex:
    def __init__(self, rows: Iterable[SynonymRow]) -> None:
        self._by_synonym: dict[str, list[SynonymRow]] = {}
        for row in rows:
            self._by_synonym.setdefault(row.synonym, []).append(row)
        self._fuzzy_keys = [
            key
            for key, rows in self._by_synonym.items()
            if any(r.origin != "local_name" for r in rows)
        ]

    def __len__(self) -> int:
        return len(self._by_synonym)

    def lookup(self, query: str) -> ResolvedFood | None:
        name = normalize_name(query)
        if not name:
            return None
        for candidate in [name, *_singular_variants(name)]:
            rows = self._by_synonym.get(candidate)
            if rows:
                return ResolvedFood(query, min(rows, key=_preference).food, "exact", 100.0)
        if len(name.split()) < FUZZY_MIN_WORDS:
            return None
        matches = process.extract(
            name,
            self._fuzzy_keys,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=FUZZY_THRESHOLD,
            limit=10,
        )
        if not matches:
            return None
        best_score = max(score for _, score, _ in matches)
        best_rows = [
            row
            for key, score, _ in matches
            if score == best_score
            for row in self._by_synonym[key]
            if row.origin != "local_name"
        ]
        return ResolvedFood(query, min(best_rows, key=_preference).food, "fuzzy", best_score)

    def resolve(self, names: Iterable[str]) -> tuple[list[ResolvedFood], list[str]]:
        """Resolved foods (each food once, in question order) and the names that matched nothing."""
        resolved: list[ResolvedFood] = []
        unresolved: list[str] = []
        seen: set[str] = set()
        for name in names:
            match = self.lookup(name)
            if match is None:
                if name.strip() and name not in unresolved:
                    unresolved.append(name)
            elif match.food.id not in seen:
                seen.add(match.food.id)
                resolved.append(match)
        return resolved, unresolved


_index: SynonymIndex | None = None


async def load_index() -> SynonymIndex:
    """The synonym index, loaded from the database on first use. An empty result is not cached."""
    global _index
    if _index is not None:
        return _index
    rows = await db.get_pool().fetch(
        """
        SELECT s.synonym, s.origin, f.id, f.name, f.dataset, f.state
        FROM food_synonyms s JOIN foods f ON f.id = s.food_id
        """
    )
    index = SynonymIndex(
        SynonymRow(
            synonym=r["synonym"],
            food=FoodEntry(id=r["id"], name=r["name"], dataset=r["dataset"], state=r["state"]),
            origin=r["origin"],
        )
        for r in rows
    )
    if len(index):
        _index = index
    return index


def clear_cache() -> None:
    global _index
    _index = None


async def resolve_foods(names: Iterable[str]) -> tuple[list[ResolvedFood], list[str]]:
    return (await load_index()).resolve(names)
