"""Quantity → grams (architecture §5.4, step 3), including the real portions.yaml."""

import pytest

from app.knowledge.units import Portion, PortionTable, normalize_unit, to_grams
from scripts.ingest.load_portions import parse_portions

PORTIONS: PortionTable = {
    (None, "katori"): Portion(150, "1 katori is taken as about 150 ml."),
    (None, "tbsp"): Portion(15, None),
    ("usda:2707713", "piece"): Portion(40, "1 medium roti is taken as about 40 g."),
    ("ifct:C033", "katori"): Portion(100, "1 katori of cooked palak ≈ 100 g raw."),
}


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("g", "g"),
        ("Grams", "grams"),
        ("katoris", "katori"),
        ("Bowl", "katori"),
        ("tablespoon", "tbsp"),
        ("tsp.", "tsp"),
        ("glasses", "glass"),
        ("rotis", "piece"),
        ("chapati", "piece"),
        ("idli", "piece"),
        ("pieces", "piece"),
        ("plate", "plate"),  # unknown units pass through and fail to convert later
    ],
)
def test_normalize_unit(unit: str, expected: str) -> None:
    assert normalize_unit(unit) == expected


@pytest.mark.parametrize(
    ("value", "unit", "grams"),
    [(100, "g", 100), (1.5, "kg", 1500), (250, "mg", 0.25), (2, "oz", 56.7), (1, "lb", 453.6)],
)
def test_metric_and_imperial_masses(value: float, unit: str, grams: float) -> None:
    conversion = to_grams(value, unit, "ifct:L003", PORTIONS)
    assert conversion is not None
    assert conversion.grams == pytest.approx(grams)
    assert conversion.note is None


def test_volumes_assume_one_gram_per_ml() -> None:
    conversion = to_grams(0.5, "litre", "ifct:L002", PORTIONS)
    assert conversion is not None
    assert conversion.grams == pytest.approx(500)
    assert conversion.note is not None and "1 ml" in conversion.note


def test_food_specific_portion_wins_over_generic() -> None:
    conversion = to_grams(1, "katori", "ifct:C033", PORTIONS)
    assert conversion is not None
    assert conversion.grams == 100
    assert conversion.note == "1 katori of cooked palak ≈ 100 g raw."
    assert conversion.description == "1 katori ≈ 100 g"


def test_generic_portion_is_used_when_the_food_has_none() -> None:
    conversion = to_grams(2, "katoris", "usda:2707427", PORTIONS)
    assert conversion is not None
    assert conversion.grams == 300


def test_countable_food_uses_its_piece_weight() -> None:
    conversion = to_grams(2, "rotis", "usda:2707713", PORTIONS)
    assert conversion is not None
    assert conversion.grams == 80
    assert conversion.description == "2 rotis ≈ 80 g"


@pytest.mark.parametrize(
    ("value", "unit", "food_id"),
    [
        (2, "piece", "ifct:L003"),  # no piece weight for paneer
        (1, "plate", "usda:2707713"),  # unknown measure
        (0, "g", "ifct:L003"),  # non-positive amount
        (-1, "katori", "ifct:C033"),
    ],
)
def test_unconvertible_quantities_return_none(value: float, unit: str, food_id: str) -> None:
    assert to_grams(value, unit, food_id, PORTIONS) is None


def test_real_portions_file_uses_canonical_measures_and_positive_weights() -> None:
    rows = parse_portions()
    generic = {measure: grams for food_id, measure, grams, _ in rows if food_id is None}
    assert generic == {"katori": 150, "cup": 200, "glass": 250, "tbsp": 15, "tsp": 5, "handful": 30}
    specific = {(food_id, measure): grams for food_id, measure, grams, _ in rows if food_id}
    assert specific[("usda:2707713", "piece")] == 40  # roti
    assert specific[("ifct:C033", "katori")] == 100  # palak, raw leaves
    assert specific[("ifct:A015", "katori")] == 50  # raw rice for 1 katori cooked
    assert all(note for _, _, _, note in rows)  # every weight states its assumption
