"""Food-safety knowledge and retrieval end to end (implementation plan Phase 4).

Real Postgres + pgvector loaded by the real ingestion scripts (the curated safety rules and
guidance documents, embedded with the test's hashing embedder), stubbed Groq. Asserts what
reaches the answer prompt and what is stored for audit.
"""

import json
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.knowledge import embeddings, retriever, safety
from app.responses import DISCLAIMER, SYMPTOMS_NOTICE
from app.schemas.answer import ChatResponse
from scripts.ingest.chunk_and_embed import chunk_and_embed
from scripts.ingest.load_safety_rules import load_safety_rules
from tests.conftest import FakeGroq, HashingEmbedder, analysis_json


def _analysis(
    foods: list[str],
    *,
    intent: str,
    category: str = "food_safety",
    location: str | None = None,
    duration: str | None = None,
    state: str | None = None,
    nutrients: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> str:
    storage = (
        {"location": location, "duration": duration, "state": state}
        if location or duration or state
        else None
    )
    return analysis_json(
        intent_summary=intent,
        category=category,
        question_type="safety_check",
        risk_flags=risk_flags or [],
        entities={
            "foods": foods,
            "nutrients": nutrients or [],
            "quantities": [],
            "storage": storage,
            "cooking_methods": [],
        },
    )


def _answer(answer: str, *claims: str) -> str:
    return json.dumps({"answer": answer, "claims": [{"text": c, "source": None} for c in claims]})


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


def _contents(snapshot: Any) -> list[str]:
    return [f["content"] for f in snapshot["facts"]]


# --- Retriever ---


async def test_retriever_returns_food_safety_chunks_for_chicken_in_the_fridge(
    knowledge: asyncpg.Pool,
) -> None:
    chunks = await retriever.retrieve("chicken in the fridge", ("food_safety",))

    assert 1 <= len(chunks) <= retriever.TOP_K
    assert {c.category for c in chunks} == {"food_safety"}
    assert "fridge" in chunks[0].text.lower()
    assert any("chicken" in c.text.lower() and "fridge" in c.text.lower() for c in chunks)
    assert chunks == sorted(chunks, key=lambda c: -c.score)
    assert all(c.origin for c in chunks)  # kept for the audit snapshot


async def test_retriever_filters_by_category(knowledge: asyncpg.Pool) -> None:
    chunks = await retriever.retrieve("chicken in the fridge", ("nutrition",))
    assert {c.category for c in chunks} <= {"nutrition"}


async def test_weak_matches_are_dropped(
    knowledge: asyncpg.Pool, fake_embedder: HashingEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_embedder, "min_score", 0.0)
    monkeypatch.setattr(fake_embedder, "score_margin", 0.1)
    chunks = await retriever.retrieve("rice left out overnight", ("food_safety",))
    assert [c.text.split(" — ")[1].split(":")[0] for c in chunks] == ["Leftover rice and the heat"]

    monkeypatch.setattr(fake_embedder, "min_score", 0.99)
    assert await retriever.retrieve("rice left out overnight", ("food_safety",)) == []


async def test_ingestion_is_idempotent(knowledge: asyncpg.Pool) -> None:
    counts_sql = """
        SELECT (SELECT count(*) FROM safety_rules), (SELECT count(*) FROM safety_food_aliases),
               (SELECT count(*) FROM doc_chunks), (SELECT count(DISTINCT dataset_version)
               FROM doc_chunks)
    """
    before = tuple(await knowledge.fetchrow(counts_sql))
    async with knowledge.acquire() as conn:
        await load_safety_rules(conn)
        await chunk_and_embed(conn, embedder=HashingEmbedder())
    assert tuple(await knowledge.fetchrow(counts_sql)) == before
    assert before[0] > 40 and before[2] > 30
    assert before[3] == 1


# --- Exit criteria scenarios ---


async def test_rice_left_out_overnight_gets_the_hot_climate_rule_and_a_verdict_fact(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["cooked rice"],
        intent="Whether cooked rice left at room temperature overnight is safe to eat.",
        location="room_temp",
        duration="overnight",
        state="cooked",
    )
    fake_groq.outcome = _answer(
        "**Not recommended — throw it away.**",
        "Cooked rice should not stay at room temperature for more than 2 hours, or 1 hour "
        "above about 32 °C.",
    )

    body = await _ask(api, "Can I eat cooked rice that was left outside overnight?")

    assert body["answer_type"] == "answer"
    assert body["category"] == "food_safety"
    snapshot = await _snapshot(knowledge)
    contents = _contents(snapshot)
    assert contents[0].startswith(
        "Cooked rice, at room temperature: limit 2 hours (1 hour above about 32 °C)."
    )
    assert "Indian conditions:" in contents[0]
    assert contents[-1] == (
        "Cooked rice kept at room temperature overnight (taken as at least 8 hours): longer "
        "than the limit of 2 hours."
    )
    assert {f["kind"] for f in snapshot["facts"]} == {"safety_rule"}
    assert snapshot["passages"]  # guidance passages retrieved too
    assert all(p["id"].startswith("P") for p in snapshot["passages"])

    prompt = _answer_prompt(fake_groq)
    assert f"[F1] {contents[0]}" in prompt
    assert "[P1] " in prompt
    for authority in ["FSSAI", "USDA", "FSIS", "WHO Five Keys"]:  # origins stay in the snapshot
        assert authority not in prompt
    assert await _failures(knowledge) == []


async def test_chicken_in_the_fridge_gets_a_specific_limit(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["chicken"], intent="How long chicken can be stored in the refrigerator.", location="fridge"
    )
    fake_groq.outcome = _answer(
        "**Raw chicken: cook within 1-2 days.**", "Raw chicken keeps 1-2 days in the fridge."
    )

    await _ask(api, "How long can chicken be stored in the refrigerator?")

    contents = _contents(await _snapshot(knowledge))
    # State unknown: both the raw and the cooked rule for the fridge, then the cooking rule.
    assert contents[0].startswith("Chicken (cooked), in the fridge: limit 3 days.")
    assert contents[1].startswith("Chicken (raw), in the fridge: limit 2 days.")
    assert contents[2].startswith("Chicken, handling and cooking:")
    assert not any("freezer" in c or "room temperature" in c for c in contents)


async def test_milk_out_during_a_power_cut_is_over_the_limit(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["milk"],
        intent="Whether milk left out during a power cut for 4 hours is safe.",
        location="room_temp",
        duration="4 hours",
    )
    fake_groq.outcome = _answer(
        "**Not recommended — throw it away.**", "Milk should not be out for more than 2 hours."
    )

    await _ask(api, "Milk was out during a power cut for 4 hours")

    contents = _contents(await _snapshot(knowledge))
    assert contents[0].startswith("Milk, at room temperature: limit 2 hours")
    assert any(
        c.startswith("Fridge and freezer during a power cut, in the fridge") for c in contents
    )
    assert (
        contents[-1]
        == "Milk kept at room temperature for 4 hours: longer than the limit of 2 hours."
    )


async def test_food_in_a_fridge_during_a_long_power_cut_uses_the_power_cut_limit(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["paneer"],
        intent="Whether paneer in the fridge is safe after a 6-hour power cut.",
        location="fridge",
        duration="6 hours",
    )
    fake_groq.outcome = _answer("**Not recommended.**", "A closed fridge stays cold 4 hours.")

    await _ask(api, "Bijli 6 ghante nahi thi, fridge mein paneer theek hai?")

    contents = _contents(await _snapshot(knowledge))
    assert contents[-1] == (
        "Power cut with the food in the fridge for 6 hours: longer than the 4 hours a closed "
        "fridge keeps food cold."
    )
    assert not any("within the limit of 3 days" in c for c in contents)


async def test_symptoms_get_the_emergency_notice(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["biryani"],
        intent="User is vomiting after eating biryani and asks what to do.",
        risk_flags=["symptoms"],
    )
    fake_groq.outcome = _answer("**Drink ORS and rest.**", "Vomiting can cause dehydration.")

    body = await _ask(api, "I am vomiting after eating biryani")

    assert body["notices"] == [SYMPTOMS_NOTICE, DISCLAIMER]
    assert "112" in body["notices"][0] and "108" in body["notices"][0]


async def test_mixed_question_gets_safety_rules_then_nutrition_facts(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["paneer"],
        intent="Whether paneer is healthy and how long it lasts in the fridge.",
        category="mixed",
        location="fridge",
    )
    fake_groq.outcome = _answer(
        "### Storage\n**Use within 2-3 days.**\n### Nutrition\nPaneer is rich in protein.",
        "Opened paneer keeps 2-3 days in the fridge.",
        "Paneer has 18.9 g protein per 100 g.",
    )

    body = await _ask(api, "Is paneer healthy and how long does it last in the fridge?")

    assert body["category"] == "mixed"
    snapshot = await _snapshot(knowledge)
    kinds = [f["kind"] for f in snapshot["facts"]]
    assert kinds[0] == "safety_rule"
    assert "nutrient" in kinds
    assert kinds.index("nutrient") > max(i for i, k in enumerate(kinds) if k == "safety_rule")
    assert snapshot["facts"][0]["content"].startswith("Paneer, in the fridge: limit 3 days.")
    assert await _failures(knowledge) == []


async def test_nutrition_questions_also_get_passages(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = analysis_json(
        intent_summary="Which foods help iron absorption.",
        category="nutrition",
        question_type="how_to",
        entities={
            "foods": [],
            "nutrients": ["iron"],
            "quantities": [],
            "storage": None,
            "cooking_methods": [],
        },
    )
    fake_groq.outcome = _answer("Eat vitamin C-rich foods with meals.", "Vitamin C helps.")

    await _ask(api, "How can I absorb more iron?")

    passages = (await _snapshot(knowledge))["passages"]
    assert passages
    assert "Iron and its absorption" in passages[0]["text"]


async def test_unknown_food_gets_an_assumption_not_invented_rules(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.analysis = _analysis(
        ["dragon fruit"], intent="How long dragon fruit lasts.", location="fridge", state="raw"
    )
    fake_groq.outcome = _answer("This could not be verified.", "Dragon fruit is perishable.")

    await _ask(api, "How long does dragon fruit last in the fridge?")

    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"] == []
    assert snapshot["assumptions"] == ["No specific food-safety rule was found for dragon fruit."]


# --- Failures: recorded, and the answer still runs with what was found ---


async def test_embedding_model_not_loaded_is_recorded_and_facts_are_kept(
    api: httpx.AsyncClient, knowledge: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    embeddings.set_embedder(None)
    fake_groq.analysis = _analysis(
        ["cooked rice"], intent="Cooked rice left out.", location="room_temp"
    )
    fake_groq.outcome = _answer("**Not recommended.**", "Rice left out spoils.")

    body = await _ask(api, "Can I eat rice left out?")

    assert body["answer_type"] == "answer"
    [failure] = await _failures(knowledge)
    assert (failure["stage"], failure["failure_type"], failure["severity"]) == (
        "retrieval",
        "retrieval_failed",
        "error",
    )
    assert "EmbeddingsUnavailableError" in failure["error_detail"]
    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"]
    assert snapshot["passages"] == []


async def test_safety_lookup_failure_is_recorded_and_passages_are_kept(
    api: httpx.AsyncClient,
    knowledge: asyncpg.Pool,
    fake_groq: FakeGroq,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_: object) -> None:
        raise ConnectionError("database went away")

    monkeypatch.setattr(safety, "fetch_rules", broken)
    fake_groq.analysis = _analysis(
        ["cooked rice"], intent="Cooked rice left at room temperature.", location="room_temp"
    )
    fake_groq.outcome = _answer("**Not recommended.**", "Rice left out spoils.")

    body = await _ask(api, "Can I eat rice left out?")

    assert body["answer_type"] == "answer"
    [failure] = await _failures(knowledge)
    assert failure["failure_type"] == "safety_lookup_failed"
    assert "database went away" in failure["error_detail"]
    snapshot = await _snapshot(knowledge)
    assert snapshot["facts"] == []
    assert snapshot["passages"]
