"""Parsing and standardization in the Phase 3 ingestion scripts (no database)."""

from pathlib import Path

import pytest

from app.knowledge.nutrients import NUTRIENTS
from scripts.ingest.common import IngestError
from scripts.ingest.load_ifct import parse_ifct, parse_local_names
from scripts.ingest.load_usda import UsdaFood, parse_usda, read_food_list

FIXTURES = Path(__file__).parents[1] / "fixtures" / "knowledge"


@pytest.fixture(scope="module")
def ifct() -> dict[str, object]:
    return {r.id: r for r in parse_ifct(FIXTURES / "ifct_compositions.csv")}


def test_ifct_values_are_standardized_to_canonical_units(ifct: dict[str, object]) -> None:
    paneer = ifct["ifct:L003"]
    nutrients = paneer.nutrients  # type: ignore[attr-defined]
    assert nutrients["protein"] == pytest.approx(18.86)
    assert nutrients["energy_kcal"] == pytest.approx(1079 / 4.184, abs=0.01)  # kJ → kcal
    assert nutrients["iron"] == pytest.approx(0.9)  # g → mg
    assert set(nutrients) <= set(NUTRIENTS)


def test_ifct_zero_energy_means_not_analysed(ifct: dict[str, object]) -> None:
    ghee = ifct["ifct:T013"]
    assert "energy_kcal" not in ghee.nutrients  # type: ignore[attr-defined]
    assert ghee.nutrients["fat"] == 100  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("food_id", "diet", "recommendable", "state"),
    [
        ("ifct:C033", "veg", True, "raw"),  # spinach
        ("ifct:L003", "veg", True, "raw"),  # paneer
        ("ifct:M004", "egg", True, "cooked"),  # boiled egg
        ("ifct:N003", "nonveg", True, "raw"),  # chicken
        ("ifct:G026", "veg", False, "raw"),  # fenugreek seeds: a spice
        ("ifct:T013", "veg", False, "raw"),  # ghee: an oil/fat
    ],
)
def test_ifct_diet_recommendable_and_state(
    ifct: dict[str, object], food_id: str, diet: str, recommendable: bool, state: str
) -> None:
    record = ifct[food_id]
    assert (record.diet, record.recommendable, record.state) == (  # type: ignore[attr-defined]
        diet,
        recommendable,
        state,
    )


def test_parse_local_names() -> None:
    lang = "A. Chira, Chiura; B., H. Poha; Kan. ?. Avalakki; Tam. Aval."
    assert parse_local_names(lang) == ["chira", "chiura", "poha", "aval"]


def test_ifct_local_names_include_hindi(ifct: dict[str, object]) -> None:
    assert "palak" in ifct["ifct:C033"].local_names  # type: ignore[attr-defined]


def test_ifct_missing_columns_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("code,name\nA001,Test\n")
    with pytest.raises(IngestError, match="missing columns"):
        parse_ifct(path)


def test_usda_survey_and_legacy_files_are_both_read() -> None:
    foods = read_food_list(FIXTURES / "usda_foods.yaml")
    records = {r.id: r for r in parse_usda(FIXTURES / "usda", foods)}
    roti = records["usda:2707713"]  # FNDDS: nutrient_id holds nutrient numbers
    assert roti.nutrients["energy_kcal"] == 299
    assert roti.nutrients["protein"] == pytest.approx(7.85)
    assert (roti.name, roti.state, roti.recommendable) == ("Roti / chapati", "cooked", False)
    rice = records["usda:169757"]  # SR Legacy: nutrient_id holds nutrient ids
    assert rice.nutrients["energy_kcal"] == 130


def test_usda_missing_fdc_id_is_an_error() -> None:
    foods = [UsdaFood("999999", "Nothing", None, "veg", "none")]
    with pytest.raises(IngestError, match="999999"):
        parse_usda(FIXTURES / "usda", foods)


def test_real_usda_food_list_is_well_formed() -> None:
    foods = read_food_list()
    assert len(foods) >= 40
    assert all(f.diet in {"veg", "egg", "nonveg"} for f in foods)
    assert all(f.state in {"raw", "cooked", None} for f in foods)
