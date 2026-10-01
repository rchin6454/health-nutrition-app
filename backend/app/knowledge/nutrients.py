"""Canonical nutrient names shared by the ingestion scripts and the knowledge layer.

Both datasets are standardized to these names and units at ingestion, so lookups never deal
with dataset-specific codes. Amounts are always per 100 g edible portion.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Nutrient:
    name: str  # canonical key stored in `nutrients.nutrient`
    label: str  # shown in facts
    unit: str  # g | mg | µg | kcal


NUTRIENTS: dict[str, Nutrient] = {
    n.name: n
    for n in [
        Nutrient("energy_kcal", "energy", "kcal"),
        Nutrient("protein", "protein", "g"),
        Nutrient("fat", "fat", "g"),
        Nutrient("saturated_fat", "saturated fat", "g"),
        Nutrient("carbohydrate", "carbohydrate", "g"),
        Nutrient("fiber", "fibre", "g"),
        Nutrient("sugars", "sugars", "g"),
        Nutrient("cholesterol", "cholesterol", "mg"),
        Nutrient("calcium", "calcium", "mg"),
        Nutrient("iron", "iron", "mg"),
        Nutrient("magnesium", "magnesium", "mg"),
        Nutrient("phosphorus", "phosphorus", "mg"),
        Nutrient("potassium", "potassium", "mg"),
        Nutrient("sodium", "sodium", "mg"),
        Nutrient("zinc", "zinc", "mg"),
        Nutrient("vitamin_c", "vitamin C", "mg"),
        Nutrient("thiamine", "thiamine (B1)", "mg"),
        Nutrient("riboflavin", "riboflavin (B2)", "mg"),
        Nutrient("niacin", "niacin (B3)", "mg"),
        Nutrient("vitamin_b6", "vitamin B6", "mg"),
        Nutrient("folate", "folate", "µg"),
        Nutrient("retinol", "retinol (vitamin A)", "µg"),
        Nutrient("beta_carotene", "beta-carotene (vitamin A precursor)", "µg"),
    ]
}

# What a user or the understanding model may call each nutrient. One alias can mean several
# canonical nutrients ("vitamin a" → retinol + beta-carotene).
_ALIASES: dict[str, tuple[str, ...]] = {
    "energy": ("energy_kcal",),
    "calories": ("energy_kcal",),
    "calorie": ("energy_kcal",),
    "kcal": ("energy_kcal",),
    "protein": ("protein",),
    "proteins": ("protein",),
    "fat": ("fat",),
    "fats": ("fat",),
    "total fat": ("fat",),
    "saturated fat": ("saturated_fat",),
    "saturated fats": ("saturated_fat",),
    "carbohydrate": ("carbohydrate",),
    "carbohydrates": ("carbohydrate",),
    "carbs": ("carbohydrate",),
    "carb": ("carbohydrate",),
    "fibre": ("fiber",),
    "fiber": ("fiber",),
    "dietary fibre": ("fiber",),
    "dietary fiber": ("fiber",),
    "sugar": ("sugars",),
    "sugars": ("sugars",),
    "cholesterol": ("cholesterol",),
    "calcium": ("calcium",),
    "iron": ("iron",),
    "magnesium": ("magnesium",),
    "phosphorus": ("phosphorus",),
    "potassium": ("potassium",),
    "sodium": ("sodium",),
    "zinc": ("zinc",),
    "vitamin c": ("vitamin_c",),
    "vit c": ("vitamin_c",),
    "ascorbic acid": ("vitamin_c",),
    "thiamine": ("thiamine",),
    "thiamin": ("thiamine",),
    "vitamin b1": ("thiamine",),
    "b1": ("thiamine",),
    "riboflavin": ("riboflavin",),
    "vitamin b2": ("riboflavin",),
    "niacin": ("niacin",),
    "vitamin b3": ("niacin",),
    "vitamin b6": ("vitamin_b6",),
    "b6": ("vitamin_b6",),
    "folate": ("folate",),
    "folic acid": ("folate",),
    "vitamin b9": ("folate",),
    "vitamin a": ("retinol", "beta_carotene"),
    "retinol": ("retinol",),
    "beta carotene": ("beta_carotene",),
    "beta-carotene": ("beta_carotene",),
}


def normalize_nutrient(name: str) -> tuple[str, ...]:
    """Canonical nutrient names for a free-text nutrient, or `()` if there is no data for it."""
    key = re.sub(r"\s+", " ", name.strip().lower().replace("_", " "))
    if key in NUTRIENTS:
        return (key,)
    return _ALIASES.get(key, ())


def format_amount(amount: float) -> str:
    """Round for display: 258, 18.9, 2.95, 0.4."""
    if amount >= 100:
        text = f"{amount:.0f}"
    elif amount >= 10:
        text = f"{amount:.1f}"
    else:
        text = f"{amount:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
