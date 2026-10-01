"""Food-safety rule lookup (architecture §5.4, step 4).

Rules are keyed by `(food_group, state, location)`. The food group comes from the analysis's
food names (word-matched against `safety_food_aliases`); state and location come from its
storage context, or from the food name ("chicken curry" is cooked). Unknown state or location is
a partial match: every rule for the other parts is returned, most specific first.

Code also compares the user's stated storage time with the matching limit ("overnight at room
temperature" vs. 2 hours) and adds the result as a fact, so the verdict never depends on the
model doing arithmetic. Rule `origin`s stay in `Fact.origin`, for audit only.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app import db
from app.knowledge.builder import ContextBuilder
from app.knowledge.entity_resolver import normalize_name
from app.schemas.analysis import QuestionAnalysis, StorageContext

State = Literal["raw", "cooked", "thawed"]
Location = Literal["room_temp", "fridge", "freezer"]

MAX_FOODS = 5
MAX_RULE_FACTS = 8
HOT_THRESHOLD_NOTE = "above about 32 °C"
# Groups whose rules for raw food also apply once the food has been thawed.
THAWABLE_GROUPS = frozenset({"chicken", "fish", "meat"})
POWER_CUT_GROUP = "power_cut"
GENERIC_GROUP = "leftovers"

_POWER_CUT = re.compile(
    r"\b(power\s*cuts?|power\s*outages?|power\s*failure|outages?|load\s*shedding|blackouts?|"
    r"no\s+(?:power|electricity|current)|bijli|light\s+(?:chali|gayi|gai|nahi)|"
    r"fridge\s+(?:was\s+)?(?:off|stopped|not\s+working|broke))",
    re.IGNORECASE,
)
_COOKED_WORDS = frozenset(
    {"cooked", "leftover", "leftovers", "boiled", "fried", "roasted", "baked", "steamed",
     "curry", "gravy", "reheated", "basi", "baasi"}
)  # fmt: skip
_RAW_WORDS = frozenset({"raw", "uncooked", "kaccha", "kacha"})
_THAWED_WORDS = frozenset({"thawed", "defrosted"})
_LOCATIONS: dict[str, Location] = {
    "fridge": "fridge",
    "refrigerator": "fridge",
    "freezer": "freezer",
    "room temp": "room_temp",  # "room_temp", after normalization
    "room temperature": "room_temp",
    "outside": "room_temp",
    "counter": "room_temp",
}
_LOCATION_PHRASES: dict[str, str] = {
    "room_temp": "at room temperature",
    "fridge": "in the fridge",
    "freezer": "in the freezer",
}


@dataclass(frozen=True)
class SafetyRule:
    id: str
    food_group: str
    food_label: str
    state: str | None
    location: str | None
    max_duration_hours: float | None
    hot_max_duration_hours: float | None
    safe_internal_temp_c: float | None
    guidance: str
    region_note: str | None
    origin: str


@dataclass(frozen=True)
class Alias:
    alias: str
    food_group: str
    implies_cooked: bool
    is_generic: bool


@dataclass(frozen=True)
class FoodMatch:
    query: str  # the food name from the analysis
    groups: tuple[str, ...]  # matched food groups, most specific first
    state: State | None


# --- Database access ---


async def fetch_aliases() -> list[Alias]:
    rows = await db.get_pool().fetch(
        "SELECT alias, food_group, implies_cooked, is_generic FROM safety_food_aliases"
    )
    return [Alias(r["alias"], r["food_group"], r["implies_cooked"], r["is_generic"]) for r in rows]


async def fetch_rules(groups: Sequence[str]) -> list[SafetyRule]:
    rows = await db.get_pool().fetch(
        """
        SELECT id, food_group, food_label, state, location, max_duration_hours,
               hot_max_duration_hours, safe_internal_temp_c, guidance, region_note, origin
        FROM safety_rules WHERE food_group = ANY($1::text[]) ORDER BY id
        """,
        list(groups),
    )
    return [
        SafetyRule(
            id=r["id"],
            food_group=r["food_group"],
            food_label=r["food_label"],
            state=r["state"],
            location=r["location"],
            max_duration_hours=_float(r["max_duration_hours"]),
            hot_max_duration_hours=_float(r["hot_max_duration_hours"]),
            safe_internal_temp_c=_float(r["safe_internal_temp_c"]),
            guidance=r["guidance"],
            region_note=r["region_note"],
            origin=r["origin"],
        )
        for r in rows
    ]


def _float(value: float | None) -> float | None:
    return None if value is None else float(value)


# --- Matching ---


def _contains(name: str, phrase: str) -> bool:
    return f" {phrase} " in f" {name} "


def _singular(name: str) -> str:
    """ "rotis" → "roti", "tomatoes" → "tomatoe" is harmless: only used as an extra probe."""
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def match_food(name: str, aliases: Iterable[Alias]) -> tuple[list[str], bool]:
    """Food groups whose aliases appear as whole words in `name`, and whether any alias says
    the food is cooked. Generic groups (leftovers) only count when nothing specific matched."""
    text = normalize_name(name)
    probes = {text, " ".join(_singular(w) for w in text.split())}
    hits = [a for a in aliases if any(_contains(p, a.alias) for p in probes)]
    specific = [a for a in hits if not a.is_generic]
    chosen = specific or hits
    # Longer aliases first: "chicken curry" names the food more precisely than "curry".
    chosen.sort(key=lambda a: -len(a.alias))
    groups: list[str] = []
    for a in chosen:
        if a.food_group not in groups:
            groups.append(a.food_group)
    return groups, any(a.implies_cooked for a in hits)


def _state_from(text: str) -> State | None:
    words = set(normalize_name(text).split())
    if words & _THAWED_WORDS:
        return "thawed"
    if words & _RAW_WORDS:
        return "raw"
    if words & _COOKED_WORDS:
        return "cooked"
    return None


def food_state(name: str, storage: StorageContext | None, implies_cooked: bool) -> State | None:
    """The food's state: the storage context first, then words in the name."""
    from_storage = _state_from(storage.state) if storage and storage.state else None
    return from_storage or _state_from(name) or ("cooked" if implies_cooked else None)


def storage_location(storage: StorageContext | None) -> Location | None:
    if storage is None or storage.location is None:
        return None
    return _LOCATIONS.get(storage.location.strip().lower().replace("-", " ").replace("_", " "))


def mentions_power_cut(*texts: str) -> bool:
    return any(_POWER_CUT.search(t) for t in texts)


def select_rules(
    rules: Iterable[SafetyRule], group: str, state: State | None, location: Location | None
) -> list[SafetyRule]:
    """Rules for `group` that fit the state and location; unknown parts match every rule.

    Ordered most specific first: the stated location, then the stated state, then handling and
    cooking rules (no location).
    """
    rule_state: str | None = state
    if state == "thawed" and group in THAWABLE_GROUPS:
        rule_state = "raw"  # thawed chicken is still raw chicken

    def fits(rule: SafetyRule) -> bool:
        if rule.food_group != group:
            return False
        if rule_state and rule.state and rule.state != rule_state:
            return False
        return not (location and rule.location and rule.location != location)

    def rank(rule: SafetyRule) -> tuple[int, int, str]:
        if location and rule.location == location:
            by_location = 0
        else:
            by_location = 2 if rule.location is None else 1
        by_state = 0 if rule_state and rule.state == rule_state else 1
        return (by_location, by_state, rule.id)

    return sorted((r for r in rules if fits(r)), key=rank)


# --- Durations ---

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "do": 2, "teen": 3, "char": 4,
    "chaar": 4, "paanch": 5,
}  # fmt: skip
_UNIT_HOURS = {
    "min": 1 / 60, "mins": 1 / 60, "minute": 1 / 60, "minutes": 1 / 60,
    "h": 1, "hr": 1, "hrs": 1, "hour": 1, "hours": 1, "ghanta": 1, "ghante": 1, "ghantey": 1,
    "day": 24, "days": 24, "din": 24,
    "week": 168, "weeks": 168, "hafta": 168, "hafte": 168,
    "month": 720, "months": 720, "mahina": 720, "mahine": 720,
}  # fmt: skip
_AMOUNT = r"\d+(?:\.\d+)?|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_DURATION = re.compile(
    rf"\b({_AMOUNT})(?:\s*(?:-|\u2013|to)\s*({_AMOUNT}))?\s*("
    + "|".join(sorted(_UNIT_HOURS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_OVERNIGHT = re.compile(r"\b(overnight|over\s*night|all\s+night|whole\s+night|raat\s*bhar)\b", re.I)
_HALF_HOUR = re.compile(r"\bhalf\s+(?:an\s+)?hour\b", re.IGNORECASE)
OVERNIGHT_HOURS = 8.0


def _amount(text: str) -> float:
    lowered = text.lower()
    return float(_NUMBER_WORDS[lowered]) if lowered in _NUMBER_WORDS else float(text)


def parse_duration_hours(text: str | None) -> float | None:
    """ "4 hours" → 4, "2-3 days" → 72 (the longer end), "overnight" → 8. None if unclear."""
    if not text:
        return None
    if _OVERNIGHT.search(text):
        return OVERNIGHT_HOURS
    if _HALF_HOUR.search(text):
        return 0.5
    match = _DURATION.search(text)
    if not match:
        return None
    amount = _amount(match.group(2) or match.group(1))
    return amount * _UNIT_HOURS[match.group(3).lower()]


def format_duration(hours: float) -> str:
    """48 → "2 days", 504 → "3 weeks", 6480 → "9 months", 0.5 → "30 minutes"."""

    def plural(n: float, unit: str) -> str:
        value = f"{n:g}"
        return f"{value} {unit}" if value == "1" else f"{value} {unit}s"

    if hours < 1:
        return plural(round(hours * 60), "minute")
    if hours < 24 or hours % 24:
        return plural(hours, "hour")
    days = hours / 24
    if days < 14:
        return plural(days, "day")
    if days % 7 == 0 and days < 60:
        return plural(days / 7, "week")
    return plural(round(days / 30), "month")


# --- Facts ---


def rule_fact(rule: SafetyRule) -> str:
    """ "Cooked rice (cooked), at room temperature: limit 2 hours (1 hour above about 32 °C). …" """
    head = rule.food_label
    if rule.state and rule.state not in rule.food_label.lower():
        head += f" ({rule.state})"
    head += f", {_LOCATION_PHRASES[rule.location]}" if rule.location else ", handling and cooking"
    parts = [f"{head}:"]
    if rule.max_duration_hours is not None:
        limit = f"limit {format_duration(rule.max_duration_hours)}"
        if rule.hot_max_duration_hours is not None:
            limit += f" ({format_duration(rule.hot_max_duration_hours)} {HOT_THRESHOLD_NOTE})"
        parts.append(f"{limit}.")
    temp = rule.safe_internal_temp_c
    if temp is not None and f"{temp:g} °C" not in rule.guidance:
        verb = "Reheat" if rule.state == "cooked" else "Cook"
        parts.append(f"{verb} to {temp:g} °C in the centre.")
    parts.append(rule.guidance)
    if rule.region_note:
        parts.append(f"Indian conditions: {rule.region_note}")
    return " ".join(parts)


def stated_storage_fact(rule: SafetyRule, duration: str, hours: float) -> str | None:
    """The user's stated storage time compared with the rule's limit, or None if no limit."""
    if rule.max_duration_hours is None or rule.location is None:
        return None
    where = _LOCATION_PHRASES[rule.location]
    stated = f"for {duration}"
    if hours == OVERNIGHT_HOURS and _OVERNIGHT.search(duration):
        stated = "overnight (taken as at least 8 hours)"
    limit = format_duration(rule.max_duration_hours)
    if rule.food_group == POWER_CUT_GROUP:
        head = f"Power cut with the food {where} {stated}"
        cold = f"a closed {rule.location} keeps food cold"
        if hours > rule.max_duration_hours:
            return f"{head}: longer than the {limit} {cold}."
        return f"{head}: within the {limit} {cold}, if the door stayed shut."
    head = f"{rule.food_label} kept {where} {stated}"
    if hours > rule.max_duration_hours:
        return f"{head}: longer than the limit of {limit}."
    hot = rule.hot_max_duration_hours
    if hot is not None and hours > hot:
        return (
            f"{head}: within the limit of {limit}, but longer than the limit of "
            f"{format_duration(hot)} {HOT_THRESHOLD_NOTE}."
        )
    return f"{head}: within the limit of {limit}."


def match_foods(analysis: QuestionAnalysis, aliases: Sequence[Alias]) -> list[FoodMatch]:
    storage = analysis.entities.storage
    matches = []
    for name in analysis.entities.foods[:MAX_FOODS]:
        groups, cooked = match_food(name, aliases)
        state = food_state(name, storage, cooked)
        if not groups and state == "cooked":
            groups = [GENERIC_GROUP]  # an unlisted cooked dish: the general leftovers rules
        if state == "thawed" and any(g in THAWABLE_GROUPS for g in groups):
            groups.append("thawed_meat")
        matches.append(FoodMatch(name, tuple(groups), state))
    return matches


async def add_safety_context(
    builder: ContextBuilder, analysis: QuestionAnalysis, question: str
) -> None:
    """Add the safety rules for the analysis's foods, storage and any power cut to `builder`."""
    storage = analysis.entities.storage
    location = storage_location(storage)
    matches = match_foods(analysis, await fetch_aliases())
    wanted: list[tuple[str, State | None]] = [(g, m.state) for m in matches for g in m.groups]
    power_cut = mentions_power_cut(question, analysis.intent_summary)
    if power_cut:
        wanted.append((POWER_CUT_GROUP, None))
    for m in matches:
        if not m.groups:
            builder.assume(f"No specific food-safety rule was found for {m.query}.")
    if not wanted:
        return

    rules = await fetch_rules(sorted({g for g, _ in wanted}))
    selected: list[SafetyRule] = []
    for group, state in wanted:
        # A power cut affects the fridge and freezer, whatever the food's stated location.
        where = None if group == POWER_CUT_GROUP else location
        selected += [r for r in select_rules(rules, group, state, where) if r not in selected]

    # One fact per rule, the stated-location rules of every food first.
    selected.sort(key=lambda r: 0 if location and r.location == location else 1)
    if len(selected) > MAX_RULE_FACTS:
        builder.assume("Only the most relevant food-safety rules are listed.")
    for rule in selected[:MAX_RULE_FACTS]:
        builder.add_fact("safety_rule", rule_fact(rule), rule.origin)

    duration = storage.duration if storage else None
    if not (duration and location):
        return
    hours = parse_duration_hours(duration)
    if hours is None:
        builder.assume(f"The storage time ({duration}) could not be compared with the limits.")
        return
    # In a fridge or freezer without power, the power-cut limit applies, not the usual one.
    compare_power_cut = power_cut and location != "room_temp"
    for rule in selected:
        if rule.location == location and (rule.food_group == POWER_CUT_GROUP) == compare_power_cut:
            content = stated_storage_fact(rule, duration, hours)
            if content:
                builder.add_fact("safety_rule", content, rule.origin)
