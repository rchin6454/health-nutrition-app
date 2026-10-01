"""Food-safety rule lookup (implementation plan Phase 4): matching, partial matches, durations.

Uses the real curated data in data/safety_rules.yaml, parsed without a database.
"""

from pathlib import Path

import pytest

from app.knowledge.safety import (
    Alias,
    SafetyRule,
    food_state,
    format_duration,
    match_food,
    mentions_power_cut,
    parse_duration_hours,
    rule_fact,
    select_rules,
    stated_storage_fact,
    storage_location,
)
from app.schemas.analysis import StorageContext
from scripts.ingest.common import IngestError
from scripts.ingest.load_safety_rules import parse_safety_rules

RULE_ROWS, ALIAS_ROWS = parse_safety_rules()
ALIASES = [Alias(a.alias, a.food_group, a.implies_cooked, a.is_generic) for a in ALIAS_ROWS]
RULES = [
    SafetyRule(
        id=r.id,
        food_group=r.food_group,
        food_label=r.food_label,
        state=r.state,
        location=r.location,
        max_duration_hours=r.max_duration_hours,
        hot_max_duration_hours=r.hot_max_duration_hours,
        safe_internal_temp_c=r.safe_internal_temp_c,
        guidance=r.guidance,
        region_note=r.region_note,
        origin=r.origin,
    )
    for r in RULE_ROWS
]
RULES_BY_ID = {r.id: r for r in RULES}


def _storage(
    location: str | None = None, duration: str | None = None, state: str | None = None
) -> StorageContext:
    return StorageContext(location=location, duration=duration, state=state)


# --- Curated data ---


def test_the_plan_s_foods_are_all_covered() -> None:
    groups = {r.food_group for r in RULES}
    for group in [
        "cooked_rice", "dal_curry", "milk", "paneer", "curd", "chicken", "fish", "eggs",
        "cut_fruit", "chutney_street_food", "thawed_meat", "leftovers", "power_cut",
    ]:  # fmt: skip
        assert group in groups


def test_every_room_temperature_limit_has_the_hot_climate_rule() -> None:
    for rule in RULES:
        if rule.location == "room_temp" and rule.max_duration_hours == 2:
            assert rule.hot_max_duration_hours == 1, rule.id
            assert rule.region_note, rule.id


@pytest.mark.parametrize("authority", ["FSSAI", "WHO", "USDA", "FSIS", "FoodKeeper", "NHS"])
def test_prompt_text_never_names_an_authority(authority: str) -> None:
    # guidance and region_note reach the prompt; the authority stays in `origin` (audit only).
    for rule in RULES:
        assert authority not in rule.guidance, rule.id
        assert authority not in (rule.region_note or ""), rule.id
        assert authority not in rule_fact(rule), rule.id


def test_every_rule_names_its_origin() -> None:
    assert all(r.origin for r in RULES)


def test_bad_rules_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "food_groups:\n  milk:\n    label: Milk\n    aliases: [milk]\n"
        "rules:\n  - id: x\n    food_group: milk\n    location: fridge\n"
        "    max_duration_hours: 48\n    hot_max_duration_hours: 1\n"
        "    guidance: g\n    origin: o\n"
    )
    with pytest.raises(IngestError, match="hot-weather limit"):
        parse_safety_rules(path)


def test_duplicate_aliases_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "food_groups:\n  a:\n    label: A\n    aliases: [milk]\n"
        "  b:\n    label: B\n    aliases: [Milk]\nrules: []\n"
    )
    with pytest.raises(IngestError, match="listed twice"):
        parse_safety_rules(path)


# --- Food matching ---


@pytest.mark.parametrize(
    ("name", "groups", "cooked"),
    [
        ("cooked rice", ["cooked_rice"], False),
        ("chawal", ["cooked_rice"], False),
        ("biryani", ["cooked_rice"], True),
        ("chicken biryani", ["chicken", "cooked_rice"], True),
        ("chicken", ["chicken"], False),
        ("Murgh", ["chicken"], False),
        ("butter chicken", ["chicken"], True),
        ("egg curry", ["eggs", "dal_curry"], True),
        ("eggs", ["eggs"], False),
        ("anda", ["eggs"], False),
        ("prawns", ["fish"], False),
        ("dahi", ["curd"], False),
        ("raita", ["curd"], False),
        ("paneer", ["paneer"], False),
        ("doodh", ["milk"], False),
        ("toor dal", ["dal_curry"], False),
        ("sambar", ["dal_curry"], True),
        ("cut papaya", ["cut_fruit"], False),
        ("pani puri", ["chutney_street_food"], False),
        ("green chutney", ["chutney_street_food"], False),
        ("keema", ["meat"], False),
        ("leftover dal", ["dal_curry"], False),  # the specific group wins over "leftover"
        ("leftovers", ["leftovers"], False),
        ("tiffin", ["leftovers"], False),
        ("mushrooms", [], False),
        ("rice flour", ["cooked_rice"], False),
    ],
)
def test_food_names_map_to_groups(name: str, groups: list[str], cooked: bool) -> None:
    assert match_food(name, ALIASES) == (groups, cooked)


def test_matching_is_by_whole_words() -> None:
    assert match_food("paneer", ALIASES)[0] == ["paneer"]  # "pani" must not match inside it
    assert match_food("fishcake", ALIASES)[0] == []


@pytest.mark.parametrize(
    ("name", "storage", "implies_cooked", "state"),
    [
        ("chicken", _storage(state="cooked"), False, "cooked"),
        ("chicken", _storage(state="raw"), False, "raw"),
        ("chicken", _storage(state="thawed"), False, "thawed"),
        ("raw chicken", None, False, "raw"),
        ("defrosted fish", None, False, "thawed"),
        ("leftover rice", None, False, "cooked"),
        ("chicken biryani", None, True, "cooked"),
        ("chicken", None, False, None),
        ("milk", _storage(state="opened"), False, None),
        ("boiled eggs", _storage(state="opened"), False, "cooked"),
    ],
)
def test_food_state(
    name: str, storage: StorageContext | None, implies_cooked: bool, state: str | None
) -> None:
    assert food_state(name, storage, implies_cooked) == state


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("fridge", "fridge"),
        ("Refrigerator", "fridge"),
        ("freezer", "freezer"),
        ("room_temp", "room_temp"),
        ("room temperature", "room_temp"),
        ("outside", "room_temp"),
        ("cupboard", None),
        (None, None),
    ],
)
def test_storage_location(location: str | None, expected: str | None) -> None:
    assert storage_location(_storage(location=location)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Milk was out during a power cut for 4 hours", True),
        ("there was a power outage last night", True),
        ("bijli chali gayi thi, doodh theek hai?", True),
        ("light gayi thi 5 ghante", True),
        ("the fridge was off all day", True),
        ("Is cut fruit safe?", False),
        ("How long does milk last?", False),
    ],
)
def test_power_cut_wording(text: str, expected: bool) -> None:
    assert mentions_power_cut(text) is expected


# --- Rule selection, including partial matches ---


def _ids(rules: list[SafetyRule]) -> list[str]:
    return [r.id for r in rules]


def test_exact_match_puts_the_stated_state_and_place_first() -> None:
    assert _ids(select_rules(RULES, "chicken", "cooked", "fridge")) == [
        "chicken_cooked_fridge",
        "chicken_cooking",
    ]


def test_unknown_state_returns_raw_and_cooked_rules_for_the_place() -> None:
    assert _ids(select_rules(RULES, "chicken", None, "fridge")) == [
        "chicken_cooked_fridge",
        "chicken_raw_fridge",
        "chicken_cooking",
    ]


def test_unknown_place_returns_every_place_for_the_state() -> None:
    assert set(_ids(select_rules(RULES, "chicken", "raw", None))) == {
        "chicken_raw_fridge",
        "chicken_raw_freezer",
        "chicken_raw_room_temp",
        "chicken_cooking",
    }


def test_unknown_state_and_place_returns_every_rule_for_the_food() -> None:
    assert set(_ids(select_rules(RULES, "cooked_rice", None, None))) == {
        "cooked_rice_room_temp",
        "cooked_rice_fridge",
        "cooked_rice_freezer",
    }


def test_thawed_chicken_uses_the_raw_chicken_rules() -> None:
    assert _ids(select_rules(RULES, "chicken", "thawed", "fridge")) == [
        "chicken_raw_fridge",
        "chicken_cooking",
    ]


def test_rules_without_a_state_apply_to_any_state() -> None:
    assert _ids(select_rules(RULES, "milk", "cooked", "fridge")) == ["milk_fridge"]
    assert _ids(select_rules(RULES, "milk", "raw", "fridge")) == [
        "milk_fridge",
        "milk_raw_handling",
    ]


def test_no_rules_for_an_unknown_group() -> None:
    assert select_rules(RULES, "dragon_fruit", None, None) == []


# --- Durations ---


@pytest.mark.parametrize(
    ("text", "hours"),
    [
        ("overnight", 8.0),
        ("raat bhar", 8.0),
        ("4 hours", 4.0),
        ("4 ghante", 4.0),
        ("2-3 days", 72.0),
        ("2\u20133 days", 72.0),  # en dash
        ("2 to 3 hours", 3.0),
        ("two days", 48.0),
        ("a week", 168.0),
        ("half an hour", 0.5),
        ("30 minutes", 0.5),
        ("1.5 hrs", 1.5),
        ("last night", 8.0),
        ("yesterday", 24.0),
        ("since yesterday", 24.0),
        ("kal", 24.0),
        ("day before yesterday", 48.0),
        ("parso", 48.0),
        ("since morning", None),
        ("a while", None),
        (None, None),
    ],
)
def test_parse_duration(text: str | None, hours: float | None) -> None:
    assert parse_duration_hours(text) == hours


@pytest.mark.parametrize(
    ("hours", "text"),
    [
        (0.5, "30 minutes"),
        (1, "1 hour"),
        (2, "2 hours"),
        (4, "4 hours"),
        (24, "1 day"),
        (48, "2 days"),
        (72, "3 days"),
        (504, "3 weeks"),
        (720, "1 month"),
        (1440, "2 months"),
        (6480, "9 months"),
    ],
)
def test_format_duration(hours: float, text: str) -> None:
    assert format_duration(hours) == text


# --- Facts ---


def test_rule_fact_gives_the_limit_the_hot_climate_rule_and_the_indian_note() -> None:
    assert rule_fact(RULES_BY_ID["cooked_rice_room_temp"]) == (
        "Cooked rice, at room temperature: limit 2 hours (1 hour above about 32 °C). "
        "Spores of Bacillus cereus survive cooking. At room temperature they grow and can form "
        "a toxin that reheating does not destroy, so rice left out overnight should be thrown "
        "away, even if it looks and smells fine. Indian conditions: Indian kitchens are often "
        "above 32 °C, especially in summer, so count 1 hour. Cool rice quickly by spreading it "
        "in a shallow container, then refrigerate it."
    )


def test_rule_fact_names_the_state_and_adds_the_cooking_temperature() -> None:
    fact = rule_fact(RULES_BY_ID["chicken_raw_fridge"])
    assert fact.startswith("Chicken (raw), in the fridge: limit 2 days. Cook raw chicken within")
    assert rule_fact(RULES_BY_ID["cooked_rice_fridge"]).startswith(
        "Cooked rice, in the fridge: limit 1 day. Reheat to 74 °C in the centre."
    )
    assert rule_fact(RULES_BY_ID["chicken_cooking"]).startswith(
        "Chicken, handling and cooking: Cook chicken until the thickest part reaches 74 °C"
    )


def test_stated_time_longer_than_the_limit() -> None:
    rule = RULES_BY_ID["cooked_rice_room_temp"]
    assert stated_storage_fact(rule, "overnight", 8.0) == (
        "Cooked rice kept at room temperature overnight (taken as at least 8 hours): longer "
        "than the limit of 2 hours."
    )
    assert stated_storage_fact(RULES_BY_ID["milk_room_temp"], "4 hours", 4.0) == (
        "Milk kept at room temperature for 4 hours: longer than the limit of 2 hours."
    )


def test_stated_time_between_the_hot_and_normal_limits() -> None:
    assert stated_storage_fact(RULES_BY_ID["milk_room_temp"], "90 minutes", 1.5) == (
        "Milk kept at room temperature for 90 minutes: within the limit of 2 hours, but longer "
        "than the limit of 1 hour above about 32 °C."
    )


def test_stated_time_within_the_limit() -> None:
    assert stated_storage_fact(RULES_BY_ID["chicken_raw_fridge"], "1 day", 24.0) == (
        "Chicken kept in the fridge for 1 day: within the limit of 2 days."
    )


def test_relative_days_are_stated_as_taken() -> None:
    # "I cooked rice yesterday": a day in the fridge is still within the 1-day rice limit.
    assert stated_storage_fact(RULES_BY_ID["cooked_rice_fridge"], "yesterday", 24.0) == (
        "Cooked rice kept in the fridge since yesterday (taken as about 1 day): "
        "within the limit of 1 day."
    )


def test_power_cut_times_are_compared_with_how_long_a_closed_fridge_stays_cold() -> None:
    rule = RULES_BY_ID["power_cut_fridge"]
    assert stated_storage_fact(rule, "6 hours", 6.0) == (
        "Power cut with the food in the fridge for 6 hours: longer than the 4 hours a closed "
        "fridge keeps food cold."
    )
    assert stated_storage_fact(rule, "2 hours", 2.0) == (
        "Power cut with the food in the fridge for 2 hours: within the 4 hours a closed fridge "
        "keeps food cold, if the door stayed shut."
    )


def test_no_comparison_for_rules_without_a_limit() -> None:
    assert stated_storage_fact(RULES_BY_ID["chicken_cooking"], "2 hours", 2.0) is None
    assert stated_storage_fact(RULES_BY_ID["eggs_raw_room_temp"], "2 days", 48.0) is None
