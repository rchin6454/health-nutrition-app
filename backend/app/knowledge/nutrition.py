"""Nutrition lookup (architecture §5.4, steps 2, 3 and 6).

Per-100 g values, scaling to the user's quantity, comparisons between foods and "foods high in X"
recommendations. Every number in a fact comes straight from the `nutrients` table; facts never
name the dataset (that stays in `Fact.origin`, for audit only).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app import db
from app.knowledge.builder import DATASET_ORIGINS, ContextBuilder
from app.knowledge.entity_resolver import FoodEntry, ResolvedFood, resolve_foods
from app.knowledge.nutrients import NUTRIENTS, format_amount, normalize_nutrient
from app.knowledge.units import Portion, PortionTable, is_grams, to_grams
from app.schemas.analysis import Quantity, QuestionAnalysis

DEFAULT_NUTRIENTS = ("energy_kcal", "protein", "fat", "carbohydrate", "fiber")
COMPARISON_NUTRIENTS = (*DEFAULT_NUTRIENTS, "iron", "calcium", "magnesium", "thiamine")
MAX_FOODS = 5
RECOMMENDATION_LIMIT = 10
# Fetched before collapsing varieties ("Brinjal 1" … "Brinjal 21") into one entry each.
_RECOMMENDATION_CANDIDATES = 60

USDA_ASSUMPTION = (
    "Values for {name} come from international reference data, not Indian food composition "
    "tables; Indian recipes and varieties may differ."
)

NutrientValues = dict[str, float]  # canonical nutrient → amount per 100 g


# --- Database access ---


async def fetch_nutrients(food_ids: Sequence[str]) -> dict[str, NutrientValues]:
    rows = await db.get_pool().fetch(
        "SELECT food_id, nutrient, amount FROM nutrients WHERE food_id = ANY($1::text[])",
        list(food_ids),
    )
    values: dict[str, NutrientValues] = {food_id: {} for food_id in food_ids}
    for r in rows:
        values[r["food_id"]][r["nutrient"]] = float(r["amount"])
    return values


async def fetch_portions(food_ids: Sequence[str]) -> PortionTable:
    rows = await db.get_pool().fetch(
        """
        SELECT food_id, measure, grams, note FROM portion_weights
        WHERE food_id IS NULL OR food_id = ANY($1::text[])
        """,
        list(food_ids),
    )
    return {(r["food_id"], r["measure"]): Portion(float(r["grams"]), r["note"]) for r in rows}


@dataclass(frozen=True)
class DietFilter:
    label: str  # "vegetarian", "vegan", "" (no restriction)
    diets: tuple[str, ...]  # allowed `foods.diet` values
    excluded_groups: tuple[str, ...]


async def top_foods(nutrient: str, diet: DietFilter) -> list[tuple[str, float]]:
    """IFCT foods richest in `nutrient` per 100 g, one entry per food (varieties collapsed)."""
    rows = await db.get_pool().fetch(
        """
        SELECT f.name, n.amount FROM nutrients n JOIN foods f ON f.id = n.food_id
        WHERE n.nutrient = $1 AND n.amount > 0 AND f.dataset = 'IFCT2017' AND f.recommendable
          AND f.diet = ANY($2::text[]) AND NOT (f.food_group = ANY($3::text[]))
        ORDER BY n.amount DESC, f.id
        LIMIT $4
        """,
        nutrient,
        list(diet.diets),
        list(diet.excluded_groups),
        _RECOMMENDATION_CANDIDATES,
    )
    top: list[tuple[str, float]] = []
    seen: set[str] = set()
    for r in rows:
        base = variety_base(r["name"])
        if base not in seen:
            seen.add(base)
            top.append((r["name"], float(r["amount"])))
    return top[:RECOMMENDATION_LIMIT]


# --- Pure helpers ---


def variety_base(name: str) -> str:
    """ "Brinjal 12" → "brinjal", "Rajmah, red" → "rajmah", "Chillies, green-3" → "chillies"."""
    first = name.split(",")[0].split(" - ")[0]
    return re.sub(r"[\s-]*\d+$", "", first).strip().lower()


def describe(food: FoodEntry) -> str:
    return f"{food.name} ({food.state})" if food.state else food.name


def requested_nutrients(names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Canonical nutrients asked for, and the names there is no data for."""
    canonical: list[str] = []
    unknown: list[str] = []
    for name in names:
        found = normalize_nutrient(name)
        if not found:
            unknown.append(name)
        canonical.extend(n for n in found if n not in canonical)
    return canonical, unknown


def nutrient_list(values: NutrientValues, nutrients: Iterable[str], scale: float = 1.0) -> str:
    """ "energy 258 kcal, protein 18.9 g" for the nutrients that have a value."""
    parts = [
        f"{NUTRIENTS[n].label} {format_amount(values[n] * scale)} {NUTRIENTS[n].unit}"
        for n in nutrients
        if n in values
    ]
    return ", ".join(parts)


def per_100g_fact(food: FoodEntry, values: NutrientValues, nutrients: Sequence[str]) -> str | None:
    listed = nutrient_list(values, nutrients)
    return f"{describe(food)}, per 100 g: {listed}." if listed else None


def scaled_fact(
    food: FoodEntry, values: NutrientValues, nutrients: Sequence[str], grams: float, portion: str
) -> str | None:
    listed = nutrient_list(values, nutrients, scale=grams / 100)
    return f"{describe(food)}, {portion}: {listed}." if listed else None


def comparison_fact(
    foods: Sequence[FoodEntry], values: dict[str, NutrientValues], nutrients: Sequence[str]
) -> str | None:
    """One line comparing every food on the nutrients they all have values for."""
    shared = [n for n in nutrients if all(n in values[f.id] for f in foods)]
    if not shared:
        return None
    names = " vs ".join(describe(f) for f in foods)
    parts = [
        f"{NUTRIENTS[n].label} "
        + " vs ".join(f"{format_amount(values[f.id][n])} {NUTRIENTS[n].unit}" for f in foods)
        for n in shared
    ]
    return f"Per 100 g, {names}: " + "; ".join(parts) + "."


def recommendation_fact(nutrient: str, diet: DietFilter, top: Sequence[tuple[str, float]]) -> str:
    info = NUTRIENTS[nutrient]
    who = f"{diet.label.capitalize()} foods" if diet.label else "Foods"
    ranked = "; ".join(
        f"{rank}. {name} {format_amount(amount)} {info.unit}"
        for rank, (name, amount) in enumerate(top, 1)
    )
    return f"{who} highest in {info.label} per 100 g (raw, as purchased): {ranked}."


def diet_filter(user_context: Sequence[str]) -> DietFilter:
    """Vegetarian unless the user says otherwise (architecture §1.3)."""
    context = {c.strip().lower().replace("_", "-") for c in user_context}
    if context & {"non-vegetarian", "non vegetarian", "nonveg", "non-veg"}:
        return DietFilter("", ("veg", "egg", "nonveg"), ())
    if context & {"eggetarian", "eggitarian"}:
        return DietFilter("vegetarian or egg", ("veg", "egg"), ())
    if "vegan" in context:
        return DietFilter("vegan", ("veg",), ("Milk and Milk Products",))
    if "jain" in context:
        return DietFilter("Jain vegetarian", ("veg",), ("Roots and Tubers",))
    return DietFilter("vegetarian", ("veg",), ())


def pair_quantities(
    food_names: Sequence[str], quantities: Sequence[Quantity]
) -> dict[str, Quantity] | None:
    """Which quantity belongs to which food name; `None` when they can't be matched up.

    The analysis lists foods and quantities separately, in question order, so they pair up
    only when there is one quantity per food.
    """
    if not quantities:
        return {}
    if len(food_names) == len(quantities):
        return dict(zip(food_names, quantities, strict=True))
    return None


# --- Context building ---


async def add_nutrition_context(builder: ContextBuilder, analysis: QuestionAnalysis) -> None:
    """Add nutrient facts for the analysis's foods, or a recommendation list, to `builder`."""
    entities = analysis.entities
    nutrients, unknown = requested_nutrients(entities.nutrients)
    for name in unknown:
        builder.assume(f"No verified values for {name} are available in the reference data.")

    resolved, unresolved = await resolve_foods(entities.foods)
    for name in unresolved:
        builder.unresolved(name)
    if len(resolved) > MAX_FOODS:
        builder.assume(f"Only the first {MAX_FOODS} foods were looked up.")
        resolved = resolved[:MAX_FOODS]

    if analysis.question_type == "recommendation" and not resolved:
        await _add_recommendations(builder, analysis, nutrients)
        return
    if not resolved:
        return

    foods = [r.food for r in resolved]
    values = await fetch_nutrients([f.id for f in foods])
    portions = await fetch_portions([f.id for f in foods])
    compare = len(foods) >= 2 and analysis.question_type == "comparison"

    if compare:
        content = comparison_fact(foods, values, nutrients or COMPARISON_NUTRIENTS)
        if content:
            builder.add_fact("comparison", content, _origin(foods))
        if len({f.state for f in foods}) > 1:
            builder.assume(
                "The compared foods are not all in the same state (raw vs cooked), so their "
                "per-100 g values are not directly comparable."
            )
    else:
        for food in foods:
            content = per_100g_fact(food, values[food.id], nutrients or DEFAULT_NUTRIENTS)
            if content:
                builder.add_fact("nutrient", content, _origin([food]))

    _add_scaled_facts(builder, resolved, values, portions, nutrients, analysis)

    for food in foods:
        missing = [n for n in nutrients if n not in values[food.id]]
        if missing:
            labels = ", ".join(NUTRIENTS[n].label for n in missing)
            builder.assume(f"No verified {labels} value is available for {food.name}.")
        if food.dataset == "USDA_FDC":
            builder.assume(USDA_ASSUMPTION.format(name=food.name))


def _add_scaled_facts(
    builder: ContextBuilder,
    resolved: Sequence[ResolvedFood],
    values: dict[str, NutrientValues],
    portions: PortionTable,
    nutrients: Sequence[str],
    analysis: QuestionAnalysis,
) -> None:
    pairs = pair_quantities(analysis.entities.foods, analysis.entities.quantities)
    if pairs is None:
        builder.assume("The quantities could not be matched to the foods; values are per 100 g.")
        return
    for r in resolved:
        quantity = pairs.get(r.query)
        if quantity is None:
            continue
        conversion = to_grams(quantity.value, quantity.unit, r.food.id, portions)
        if conversion is None:
            builder.assume(
                f'"{format_amount(quantity.value)} {quantity.unit}" of {r.food.name} could not '
                "be converted to grams; values are per 100 g."
            )
            continue
        if conversion.note:
            builder.assume(conversion.note)
        if is_grams(quantity.unit) and abs(conversion.grams - 100) < 1e-9:
            continue  # "100 g": the per-100 g fact already covers it
        content = scaled_fact(
            r.food,
            values[r.food.id],
            nutrients or DEFAULT_NUTRIENTS,
            conversion.grams,
            conversion.description,
        )
        if content:
            builder.add_fact("nutrient", content, _origin([r.food]))


async def _add_recommendations(
    builder: ContextBuilder, analysis: QuestionAnalysis, nutrients: Sequence[str]
) -> None:
    if not nutrients:
        return
    if re.search(r"\b(low|lowest|less|least)\b", analysis.intent_summary.lower()):
        return  # "low in X" lists by per-100 g amount are misleading (water, cucumber)
    diet = diet_filter(analysis.user_context)
    for nutrient in nutrients[:2]:
        top = await top_foods(nutrient, diet)
        if top:
            builder.add_fact(
                "recommendation",
                recommendation_fact(nutrient, diet, top),
                DATASET_ORIGINS["IFCT2017"],
            )
    if diet.label == "vegetarian":
        builder.assume("The list is vegetarian because the user did not say otherwise.")


def _origin(foods: Iterable[FoodEntry]) -> str:
    return ", ".join(sorted({DATASET_ORIGINS.get(f.dataset, f.dataset) for f in foods}))
