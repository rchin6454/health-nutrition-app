"""Food-name resolution (architecture §5.4, step 1), against the real curated synonyms file."""

from pathlib import Path

import pytest

from app.knowledge.entity_resolver import (
    FoodEntry,
    SynonymIndex,
    SynonymRow,
    normalize_name,
)
from scripts.ingest.build_synonyms import build_rows, parse_curated
from scripts.ingest.common import IngestError


def _food(food_id: str, name: str | None = None) -> FoodEntry:
    dataset = "IFCT2017" if food_id.startswith("ifct:") else "USDA_FDC"
    return FoodEntry(id=food_id, name=name or food_id, dataset=dataset, state=None)


def _index(rows: list[tuple[str, str, str]], names: dict[str, str] | None = None) -> SynonymIndex:
    names = names or {}
    return SynonymIndex(
        SynonymRow(synonym=s, food=_food(f, names.get(f)), origin=o) for s, f, o in rows
    )


@pytest.fixture(scope="module")
def curated_index() -> SynonymIndex:
    curated = parse_curated()
    food_names = {food_id: food_id for food_id in set(curated.values())}
    return _index(build_rows(curated, food_names, {}))


# At least 30 Indian food names (Hindi, regional, Hinglish and the English forms the
# understanding step normalizes to) → the food they must resolve to.
INDIAN_NAMES = [
    ("paneer", "ifct:L003"),
    ("chhena", "ifct:L003"),
    ("dahi", "usda:171284"),
    ("curd", "usda:171284"),
    ("chawal", "ifct:A015"),
    ("rice", "ifct:A015"),
    ("cooked rice", "usda:169757"),
    ("bhaat", "usda:169757"),
    ("brown rice", "ifct:A013"),
    ("baingan", "ifct:D031"),
    ("brinjal", "ifct:D031"),
    ("bhindi", "ifct:D056"),
    ("okra", "ifct:D056"),
    ("ladies finger", "ifct:D056"),
    ("arhar dal", "ifct:B021"),
    ("toor dal", "ifct:B021"),
    ("tuvar dal", "ifct:B021"),
    ("rajma", "ifct:B020"),
    ("atta", "ifct:A019"),
    ("whole wheat flour", "ifct:A019"),
    ("maida", "ifct:A018"),
    ("suji", "ifct:A022"),
    ("rava", "ifct:A022"),
    ("palak", "ifct:C033"),
    ("spinach", "ifct:C033"),
    ("methi", "ifct:C020"),
    ("fenugreek leaves", "ifct:C020"),
    ("methi dana", "ifct:G026"),
    ("ragi", "ifct:A010"),
    ("nachni", "ifct:A010"),
    ("bajra", "ifct:A003"),
    ("jowar", "ifct:A005"),
    ("poha", "ifct:A011"),
    ("murmura", "ifct:A012"),
    ("moong dal", "ifct:B010"),
    ("masoor dal", "ifct:B013"),
    ("kala chana", "ifct:B002"),
    ("chole", "usda:173757"),
    ("kulthi", "ifct:B012"),
    ("lauki", "ifct:D007"),
    ("karela", "ifct:D004"),
    ("gobhi", "ifct:D036"),
    ("aloo", "ifct:F006"),
    ("shakarkandi", "ifct:F013"),
    ("amla", "ifct:E021"),
    ("amrood", "ifct:E028"),
    ("kela", "ifct:E012"),
    ("anar", "ifct:E055"),
    ("badam", "ifct:H001"),
    ("moongphali", "ifct:H012"),
    ("halim seeds", "ifct:H008"),
    ("gur", "ifct:I001"),
    ("doodh", "ifct:L002"),
    ("anda", "ifct:M001"),
    ("desi ghee", "ifct:T013"),
    ("sarson ka tel", "ifct:T006"),
    ("roti", "usda:2707713"),
    ("phulka", "usda:2707713"),
    ("idli", "usda:2708346"),
    ("dosa", "usda:2708347"),
    ("chaas", "usda:170874"),
    ("besan", "usda:174288"),
    ("makhana", "usda:170149"),
    ("sabudana", "usda:169717"),
]


def test_at_least_30_indian_names_are_covered() -> None:
    assert len(INDIAN_NAMES) >= 30


@pytest.mark.parametrize(("name", "food_id"), INDIAN_NAMES)
def test_indian_names_resolve_exactly(curated_index: SynonymIndex, name: str, food_id: str) -> None:
    match = curated_index.lookup(name)
    assert match is not None, name
    assert (match.food.id, match.match) == (food_id, "exact")


@pytest.mark.parametrize(
    ("name", "food_id"),
    [
        ("Rotis", "usda:2707713"),  # case and plural
        ("idlis", "usda:2708346"),
        ("  Toor   Dal ", "ifct:B021"),  # whitespace
        ("whole-wheat flour", "ifct:A019"),  # punctuation
        ("tomatoes", "ifct:D076"),
    ],
)
def test_names_are_normalized_before_matching(
    curated_index: SynonymIndex, name: str, food_id: str
) -> None:
    match = curated_index.lookup(name)
    assert match is not None
    assert match.food.id == food_id


def test_multi_word_names_fall_back_to_fuzzy_matching(curated_index: SynonymIndex) -> None:
    match = curated_index.lookup("rice brown")  # word order differs from "brown rice"
    assert match is not None
    assert (match.food.id, match.match) == ("ifct:A013", "fuzzy")


@pytest.mark.parametrize("name", ["kheer", "kale", "dragon fruit smoothie", "unicorn meat", ""])
def test_unknown_foods_are_unresolved(curated_index: SynonymIndex, name: str) -> None:
    assert curated_index.lookup(name) is None


def test_single_words_never_fuzzy_match_a_different_food() -> None:
    """ "kheer" (rice pudding) is one letter away from "kheera" (cucumber)."""
    index = _index([("kheera", "ifct:D043", "curated"), ("makhan", "usda:173410", "curated")])
    assert index.lookup("kheer") is None
    assert index.lookup("makhana") is None


def test_fuzzy_matching_ignores_local_language_names() -> None:
    index = _index([("green gram whole", "ifct:B011", "local_name")])
    assert index.lookup("whole green grams") is None
    assert index.lookup("green gram whole") is not None  # exact matches still use them


def test_curated_synonyms_win_over_derived_names() -> None:
    index = _index([("chawal", "ifct:A013", "local_name"), ("chawal", "ifct:A015", "curated")])
    match = index.lookup("chawal")
    assert match is not None
    assert match.food.id == "ifct:A015"


def test_ifct_is_preferred_over_usda_for_the_same_name() -> None:
    index = _index([("ghee", "usda:171314", "curated"), ("ghee", "ifct:T013", "curated")])
    match = index.lookup("ghee")
    assert match is not None
    assert match.food.id == "ifct:T013"


def test_resolve_dedupes_foods_and_lists_unresolved_names(curated_index: SynonymIndex) -> None:
    resolved, unresolved = curated_index.resolve(
        ["paneer", "chhena", "dragon fruit", "palak", "dragon fruit"]
    )
    assert [r.food.id for r in resolved] == ["ifct:L003", "ifct:C033"]
    assert [r.query for r in resolved] == ["paneer", "palak"]
    assert unresolved == ["dragon fruit"]


def test_normalize_name() -> None:
    assert normalize_name("Rice, raw (brown)") == "rice raw brown"
    assert normalize_name("  Cow's   MILK ") == "cow s milk"


# --- build_synonyms: how the synonym rows are derived ---


def test_derived_names_mapping_to_several_foods_are_dropped() -> None:
    rows = build_rows(
        curated={},
        food_names={"ifct:A013": "Rice, raw, brown", "ifct:A015": "Rice, raw, milled"},
        local_names={"ifct:A013": ["chawal", "bhura chaval"], "ifct:A015": ["chawal"]},
    )
    assert ("chawal", "ifct:A013", "local_name") not in rows
    assert ("bhura chaval", "ifct:A013", "local_name") in rows
    assert ("rice raw brown", "ifct:A013", "dataset_name") in rows


def test_derived_names_never_duplicate_a_curated_name() -> None:
    rows = build_rows(
        curated={"paneer": "ifct:L003"},
        food_names={"ifct:L003": "Paneer"},
        local_names={"ifct:L003": ["paneer", "chhena"]},
    )
    assert [r for r in rows if r[0] == "paneer"] == [("paneer", "ifct:L003", "curated")]


def test_curated_synonyms_for_unloaded_foods_are_skipped() -> None:
    rows = build_rows(curated={"paneer": "ifct:L003"}, food_names={}, local_names={})
    assert rows == []


def test_a_curated_name_listed_under_two_foods_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "synonyms.yaml"
    path.write_text("synonyms:\n  ifct:A013: [chawal]\n  ifct:A015: [Chawal]\n")
    with pytest.raises(IngestError, match=r"(?i)chawal.*ifct:A013 and ifct:A015"):
        parse_curated(path)
