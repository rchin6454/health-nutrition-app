"""Quantity → grams (architecture §5.4, step 3). Pure Python, no database access.

Metric units convert directly. Indian household measures (katori, glass, roti, …) use
`portion_weights`: a food-specific weight first ("1 katori of cooked rice"), then the generic one.
"""

import re
from dataclasses import dataclass

from app.knowledge.nutrients import format_amount

# Metric and imperial units → grams per unit. Volumes assume 1 ml ≈ 1 g (stated to the user).
_MASS_UNITS: dict[str, float] = {
    "g": 1.0,
    "gm": 1.0,
    "gms": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "gramme": 1.0,
    "kg": 1000.0,
    "kgs": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "mg": 0.001,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "lb": 453.6,
    "lbs": 453.6,
    "pound": 453.6,
    "pounds": 453.6,
}
_VOLUME_UNITS: dict[str, float] = {
    "ml": 1.0,
    "millilitre": 1.0,
    "milliliter": 1.0,
    "millilitres": 1.0,
    "milliliters": 1.0,
    "l": 1000.0,
    "litre": 1000.0,
    "liter": 1000.0,
    "litres": 1000.0,
    "liters": 1000.0,
}

# Household measure aliases → the `portion_weights.measure` they use.
_MEASURE_ALIASES: dict[str, str] = {
    "katori": "katori",
    "katoris": "katori",
    "bowl": "katori",
    "bowls": "katori",
    "small bowl": "katori",
    "vati": "katori",
    "cup": "cup",
    "cups": "cup",
    "glass": "glass",
    "glasses": "glass",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tbs": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "chammach": "tbsp",
    "spoon": "tbsp",
    "spoons": "tbsp",
    "handful": "handful",
    "handfuls": "handful",
    "mutthi": "handful",
    "slice": "slice",
    "slices": "slice",
    # Countable foods: "2 rotis", "3 idlis", "1 egg" are all pieces of the food itself.
    "piece": "piece",
    "pieces": "piece",
    "pc": "piece",
    "pcs": "piece",
    "no": "piece",
    "nos": "piece",
    "number": "piece",
    "whole": "piece",
    "item": "piece",
    "items": "piece",
    "roti": "piece",
    "rotis": "piece",
    "chapati": "piece",
    "chapatis": "piece",
    "chapatti": "piece",
    "chapattis": "piece",
    "phulka": "piece",
    "phulkas": "piece",
    "paratha": "piece",
    "parathas": "piece",
    "puri": "piece",
    "puris": "piece",
    "poori": "piece",
    "pooris": "piece",
    "idli": "piece",
    "idlis": "piece",
    "dosa": "piece",
    "dosas": "piece",
    "naan": "piece",
    "naans": "piece",
    "samosa": "piece",
    "samosas": "piece",
    "vada": "piece",
    "vadas": "piece",
    "egg": "piece",
    "eggs": "piece",
    "banana": "piece",
    "bananas": "piece",
    "apple": "piece",
    "apples": "piece",
}


@dataclass(frozen=True)
class Portion:
    grams: float
    note: str | None  # the assumption behind the weight, shown to the user


# (food_id or None for generic, measure) → Portion
PortionTable = dict[tuple[str | None, str], Portion]


@dataclass(frozen=True)
class Conversion:
    grams: float
    description: str  # "2 roti ≈ 80 g"
    note: str | None  # assumption to state, if any


def normalize_unit(unit: str) -> str:
    """ "Katoris" → "katori", "Tablespoon" → "tbsp", "rotis" → "piece", "g" → "g"."""
    key = " ".join(re.sub(r"[^\w\s]", " ", unit.lower()).split())
    if key in _MASS_UNITS or key in _VOLUME_UNITS:
        return key
    return _MEASURE_ALIASES.get(key, key)


def is_grams(unit: str) -> bool:
    """True for "g", "grams", "gm" …: a quantity already given in grams."""
    return _MASS_UNITS.get(normalize_unit(unit)) == 1.0


def to_grams(value: float, unit: str, food_id: str, portions: PortionTable) -> Conversion | None:
    """Convert `value` `unit` of a food to grams, or `None` if the unit is unknown for it."""
    if value <= 0:
        return None
    measure = normalize_unit(unit)
    amount = format_amount(value)
    if measure in _MASS_UNITS:
        grams = value * _MASS_UNITS[measure]
        return Conversion(grams, f"{amount} {unit.strip()} = {format_amount(grams)} g", None)
    if measure in _VOLUME_UNITS:
        grams = value * _VOLUME_UNITS[measure]
        return Conversion(
            grams,
            f"{amount} {unit.strip()} ≈ {format_amount(grams)} g",
            "Volumes are converted assuming 1 ml weighs about 1 g.",
        )
    portion = portions.get((food_id, measure)) or portions.get((None, measure))
    if portion is None:
        return None
    grams = value * portion.grams
    return Conversion(grams, f"{amount} {unit.strip()} ≈ {format_amount(grams)} g", portion.note)
