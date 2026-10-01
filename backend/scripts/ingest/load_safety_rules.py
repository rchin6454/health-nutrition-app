"""data/safety_rules.yaml → `safety_rules` + `safety_food_aliases` (FSSAI-first food safety).

Usage (from backend/):  uv run python -m scripts.ingest.load_safety_rules
"""

from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Any

import asyncpg

from app.knowledge.entity_resolver import normalize_name
from scripts.ingest.common import SAFETY_RULES_YAML, IngestError, dataset_version, load_yaml, run

STATES = {None, "raw", "cooked", "thawed"}
LOCATIONS = {None, "room_temp", "fridge", "freezer"}


@dataclass(frozen=True)
class RuleRow:
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
class AliasRow:
    alias: str
    food_group: str
    implies_cooked: bool
    is_generic: bool


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _rule(spec: dict[str, Any], labels: dict[str, str]) -> RuleRow:
    rule_id = spec["id"]
    group = spec["food_group"]
    if group not in labels:
        raise IngestError(f"rule {rule_id}: unknown food_group {group!r}")
    row = RuleRow(
        id=rule_id,
        food_group=group,
        food_label=labels[group],
        state=spec.get("state"),
        location=spec.get("location"),
        max_duration_hours=_optional_float(spec.get("max_duration_hours")),
        hot_max_duration_hours=_optional_float(spec.get("hot_max_duration_hours")),
        safe_internal_temp_c=_optional_float(spec.get("safe_internal_temp_c")),
        guidance=" ".join(str(spec["guidance"]).split()),
        region_note=" ".join(str(spec["region_note"]).split()) if spec.get("region_note") else None,
        origin=" ".join(str(spec["origin"]).split()),
    )
    if row.state not in STATES:
        raise IngestError(f"rule {rule_id}: unknown state {row.state!r}")
    if row.location not in LOCATIONS:
        raise IngestError(f"rule {rule_id}: unknown location {row.location!r}")
    if row.hot_max_duration_hours is not None:
        if row.location != "room_temp" or row.max_duration_hours is None:
            raise IngestError(f"rule {rule_id}: a hot-weather limit needs a room_temp limit")
        if row.hot_max_duration_hours > row.max_duration_hours:
            raise IngestError(f"rule {rule_id}: the hot-weather limit must be the stricter one")
    return row


def parse_safety_rules(path: Path = SAFETY_RULES_YAML) -> tuple[list[RuleRow], list[AliasRow]]:
    data: Any = load_yaml(path)
    groups: dict[str, Any] = data["food_groups"]
    labels = {group: spec["label"] for group, spec in groups.items()}

    aliases: dict[str, AliasRow] = {}
    for group, spec in groups.items():
        generic = bool(spec.get("generic", False))
        names = [(n, False) for n in spec.get("aliases", [])]
        names += [(n, True) for n in spec.get("cooked_aliases", [])]
        for name, cooked in names:
            alias = normalize_name(name)
            if alias in aliases:
                raise IngestError(f"alias {alias!r} is listed twice ({group})")
            aliases[alias] = AliasRow(alias, group, cooked, generic)

    rules = [_rule(spec, labels) for spec in data["rules"]]
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise IngestError("rule ids must be unique")
    return rules, list(aliases.values())


async def load_safety_rules(conn: asyncpg.Connection, path: Path = SAFETY_RULES_YAML) -> str:
    rules, aliases = parse_safety_rules(path)
    version = dataset_version("safety", path)
    async with conn.transaction():
        await conn.execute("DELETE FROM safety_rules")
        await conn.execute("DELETE FROM safety_food_aliases")
        await conn.executemany(
            """
            INSERT INTO safety_rules (id, food_group, food_label, state, location,
                max_duration_hours, hot_max_duration_hours, safe_internal_temp_c, guidance,
                region_note, origin, dataset_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            [(*astuple(r), version) for r in rules],
        )
        await conn.executemany(
            """
            INSERT INTO safety_food_aliases (alias, food_group, implies_cooked, is_generic,
                dataset_version)
            VALUES ($1, $2, $3, $4, $5)
            """,
            [(*astuple(a), version) for a in aliases],
        )
    return f"safety rules {version}: {len(rules)} rules, {len(aliases)} food names"


if __name__ == "__main__":
    run(load_safety_rules)
