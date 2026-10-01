"""Nutrition knowledge end to end (implementation plan Phase 3).

Real Postgres loaded by the real ingestion scripts (small fixture datasets + the real curated
YAML), stubbed Groq. Asserts what reaches the answer prompt and what is stored for audit.
"""

import json
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.pipeline import orchestrator
from app.pipeline.prompt_builder import EMPTY_CONTEXT
from app.schemas.answer import ChatResponse
from tests.conftest import FakeGroq, analysis_json, load_knowledge_fixtures


def _analysis(
    foods: list[str],
    *,
    nutrients: list[str] | None = None,
    quantities: list[dict[str, Any]] | None = None,
    question_type: str = "lookup",
    category: str = "nutrition",
    user_context: list[str] | None = None,
) -> str:
    return analysis_json(
        category=category,
        question_type=question_type,
        user_context=user_context or [],
        entities={
            "foods": foods,
            "nutrients": nutrients or [],
            "quantities": quantities or [],
            "storage": None,
            "cooking_methods": [],
        },
    )


def _answer(*claims: str) -> str:
    return json.dumps(
        {"answer": "Answer text.", "claims": [{"text": c, "source": None} for c in claims]}
    )


async def _ask(api: httpx.AsyncClient, message: str) -> dict[str, Any]:
    resp = await api.post("/api/chat", json={"conversation_id": str(uuid4()), "message": message})
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    ChatResponse.model_validate(body)
    assert all(claim["source"] is None for claim in body["claims"])  # R4
    return body


def _answer_prompt(fake: FakeGroq) -> str:
    [call] = [c for c in fake.calls if c["response_format"]["json_schema"]["name"] == "llm_answer"]
    content: str = call["messages"][-1]["content"]
    return content


async def _snapshot(pool: asyncpg.Pool) -> Any:
    return await pool.fetchval(
        "SELECT context_snapshot FROM messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1"
    )


async def _failures(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM failures ORDER BY id")


async def test_paneer_protein_is_grounded_in_the_ifct_fact(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["paneer"], nutrients=["protein"], quantities=[{"value": 100, "unit": "g"}]
    )
    fake_groq.outcome = _answer("Paneer has 18.9 g of protein per 100 g.")

    body = await _ask(api, "How much protein is there in 100g of paneer?")

    assert body["answer_type"] == "answer"
    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"] == [
        {
            "id": "F1",
            "kind": "nutrient",
            "content": "Paneer (raw), per 100 g: protein 18.9 g.",
            "origin": "IFCT 2017 (ICMR-NIN)",
        }
    ]
    prompt = _answer_prompt(fake_groq)
    assert "<context>\n[F1] Paneer (raw), per 100 g: protein 18.9 g." in prompt
    assert "IFCT" not in prompt  # dataset names stay in the snapshot only
    assert await _failures(knowledge) == []  # the claim's number is in the facts


async def test_unknown_food_is_listed_as_unresolved(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(["dragon fruit smoothie"], nutrients=["energy"])
    fake_groq.outcome = _answer("The calories in this drink could not be verified.")

    await _ask(api, "Calories in a dragon fruit smoothie?")

    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"] == []
    assert snapshot["unresolved_entities"] == ["dragon fruit smoothie"]
    prompt = _answer_prompt(fake_groq)
    assert "No verified data found for: dragon fruit smoothie\n</context>" in prompt


async def test_two_rotis_use_the_roti_piece_weight(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["roti"], nutrients=["energy"], quantities=[{"value": 2, "unit": "roti"}]
    )
    fake_groq.outcome = _answer("Two medium rotis provide about 239 kcal.")

    await _ask(api, "Calories in 2 rotis")

    snapshot = await _snapshot(knowledge)
    assert [f["content"] for f in snapshot["facts"]] == [
        "Roti / chapati (cooked), per 100 g: energy 299 kcal.",
        "Roti / chapati (cooked), 2 roti ≈ 80 g: energy 239 kcal.",
    ]
    assert snapshot["assumptions"] == [
        "1 medium roti (about 7 inches across) is taken as about 40 g.",
        "Values for Roti / chapati come from international reference data, not Indian food "
        "composition tables; Indian recipes and varieties may differ.",
    ]
    assert await _failures(knowledge) == []


async def test_one_katori_palak_uses_the_raw_leaf_weight(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["spinach"], nutrients=["iron"], quantities=[{"value": 1, "unit": "katori"}]
    )
    fake_groq.outcome = _answer("One katori of palak has about 2.95 mg of iron.")

    await _ask(api, "iron in 1 katori palak")

    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"][-1]["content"] == "Spinach (raw), 1 katori ≈ 100 g: iron 2.95 mg."
    assert any("100 g of raw spinach leaves" in a for a in snapshot["assumptions"])


async def test_iron_rich_foods_are_vegetarian_by_default(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis([], nutrients=["iron"], question_type="recommendation")
    fake_groq.outcome = _answer("Garden cress seeds have 17.2 mg iron per 100 g.")

    await _ask(api, "What foods are high in iron?")

    [fact] = (await _snapshot(knowledge))["facts"]
    assert fact["kind"] == "recommendation"
    assert fact["content"].startswith(
        "Vegetarian foods highest in iron per 100 g (raw, as purchased): "
        "1. Garden cress, seeds 17.2 mg; 2. Horse gram, whole 8.76 mg;"
    )
    for excluded in ["Chicken", "Egg", "Fenugreek seeds"]:  # non-veg, egg, a spice
        assert excluded not in fact["content"]


async def test_non_vegetarian_users_get_every_food(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        [], nutrients=["protein"], question_type="recommendation", user_context=["non-vegetarian"]
    )
    fake_groq.outcome = _answer("Soya bean has 37.8 g protein per 100 g.")

    await _ask(api, "Non-veg foods high in protein?")

    [fact] = (await _snapshot(knowledge))["facts"]
    assert fact["content"].startswith("Foods highest in protein")
    assert "Chicken" in fact["content"]


async def test_brown_vs_white_rice_uses_comparison_facts(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(["brown rice", "white rice"], question_type="comparison")
    fake_groq.outcome = _answer("Brown rice has 4.43 g fibre per 100 g vs 2.81 g in white rice.")

    await _ask(api, "Is brown rice healthier than white rice?")

    [fact] = (await _snapshot(knowledge))["facts"]
    assert fact["kind"] == "comparison"
    assert fact["content"].startswith(
        "Per 100 g, Rice, raw, brown (raw) vs Rice, raw, milled (raw): energy 354 kcal vs 356 kcal;"
    )
    assert "fibre 4.43 g vs 2.81 g" in fact["content"]
    assert await _failures(knowledge) == []


async def test_unverified_number_is_a_warning_and_the_answer_is_unchanged(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(["paneer"], nutrients=["protein"])
    fake_groq.outcome = _answer("Paneer has 25 g of protein per 100 g.")

    body = await _ask(api, "How much protein is in paneer?")

    assert body["answer_type"] == "answer"
    assert body["claims"] == [{"text": "Paneer has 25 g of protein per 100 g.", "source": None}]
    [failure] = await _failures(knowledge)
    assert (failure["stage"], failure["failure_type"], failure["severity"]) == (
        "validation",
        "unverified_number",
        "warning",
    )
    assert failure["error_detail"] == "claims[0]: 25 g"
    assert failure["raw_output"] == fake_groq.outcome


async def test_food_safety_questions_skip_the_nutrition_lookup(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(["cooked rice"], category="food_safety")
    fake_groq.outcome = _answer("Cooked rice left out overnight should be discarded.")

    await _ask(api, "Can I eat rice left out overnight?")

    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"]
    assert {f["kind"] for f in snapshot["facts"]} == {"safety_rule"}  # no nutrient facts


async def test_knowledge_failure_is_recorded_and_the_answer_still_runs(
    api: httpx.AsyncClient,
    knowledge: asyncpg.Pool,
    fake_groq: FakeGroq,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*_: object) -> None:
        raise ConnectionError("database went away")

    monkeypatch.setattr(orchestrator, "build_context", broken)
    fake_groq.outcome = _answer("Brown rice keeps its bran layer.")

    body = await _ask(api, "Is brown rice healthier than white rice?")

    assert body["answer_type"] == "answer"
    failure, warning = await _failures(knowledge)
    assert (failure["stage"], failure["failure_type"], failure["severity"]) == (
        "retrieval",
        "knowledge_lookup_failed",
        "error",
    )
    assert "database went away" in failure["error_detail"]
    # With no context, an answer that does not say so is flagged (the response is unchanged).
    assert (warning["failure_type"], warning["severity"]) == (
        "unsupported_without_context",
        "warning",
    )
    assert await _snapshot(knowledge) is None
    assert f"<context>\n{EMPTY_CONTEXT}\n</context>" in _answer_prompt(fake_groq)


async def test_ingestion_is_idempotent_and_keeps_synonyms_on_reload(
    knowledge: asyncpg.Pool,
) -> None:
    counts_sql = """
        SELECT (SELECT count(*) FROM foods), (SELECT count(*) FROM nutrients),
               (SELECT count(*) FROM food_synonyms), (SELECT count(*) FROM portion_weights),
               (SELECT count(DISTINCT dataset_version) FROM foods)
    """
    before = tuple(await knowledge.fetchrow(counts_sql))
    async with knowledge.acquire() as conn:
        await load_knowledge_fixtures(conn)
    assert tuple(await knowledge.fetchrow(counts_sql)) == before
    assert before[0] == 25  # 21 IFCT + 4 USDA fixture foods
    assert before[4] == 2  # one version per dataset
