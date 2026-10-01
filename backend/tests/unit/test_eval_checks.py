"""The eval suite's case file and deterministic checks (tests/evals/), without calling Groq."""

from typing import Any

import pytest

from app.scope.input_gate import check_input
from tests.evals.cases import Case, load_cases
from tests.evals.checks import Outcome, run_checks, verdict_of
from tests.evals.run_evals import summarize

CASES = load_cases()


def _response(**overrides: Any) -> dict[str, Any]:
    response: dict[str, Any] = {
        "schema_version": "1.0",
        "request_id": "r",
        "conversation_id": "c",
        "answer_type": "answer",
        "category": "nutrition",
        "answer": "Paneer has **18.9 g** of protein per 100 g.",
        "claims": [{"text": "Paneer (raw) has 18.9 g protein per 100 g.", "source": None}],
        "notices": ["General information, not medical advice."],
    }
    response.update(overrides)
    return response


def _case(**expect: Any) -> Case:
    return Case.model_validate(
        {"id": "t", "slice": "nutrition", "question": "q?", "expect": expect}
    )


def _results(case: Case, outcome: Outcome) -> dict[str, tuple[bool, str]]:
    return {r.name: (r.passed, r.detail) for r in run_checks(case, outcome)}


# --- The case file ---


def test_the_suite_has_100_to_150_cases_in_every_slice() -> None:
    assert 100 <= len(CASES) <= 150
    assert {c.slice for c in CASES} == {
        "classification",
        "nutrition",
        "food_safety",
        "clarification",
        "scope",
        "indian_context",
        "uncertainty",
    }


def test_the_five_problem_statement_examples_are_smoke_cases() -> None:
    smoke = {c.question for c in CASES if c.smoke}
    assert {
        "Is brown rice healthier than white rice?",
        "How much protein is there in 100g of paneer?",
        "Can I eat cooked rice that was left outside overnight?",
        "What foods are high in iron?",
        "How long can chicken be stored in the refrigerator?",
    } <= smoke


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.expect.blocked_by == "input_gate"], ids=lambda c: c.id
)
def test_input_gate_cases_are_blocked_by_code(case: Case) -> None:
    # Deterministic, so CI checks it on every push; the eval run checks it end to end.
    assert check_input(case.question).rejection is not None


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c.expect.blocked_by != "input_gate" and c.slice != "scope"],
    ids=lambda c: c.id,
)
def test_answerable_cases_pass_the_input_gate(case: Case) -> None:
    assert check_input(case.question).rejection is None


# --- Checks ---


def test_a_good_answer_passes_every_check() -> None:
    case = _case(
        category="nutrition",
        answer_type="answer",
        numbers=[{"label": "protein", "value": 18.86}],
        mentions=["protein"],
        forbid=["chicken"],
    )
    outcome = Outcome(
        response=_response(),
        analysis_category="nutrition",
        groq_requests={"understanding": 1, "answer": 1},
        model_outputs=2,
    )
    results = _results(case, outcome)
    assert all(passed for passed, _ in results.values()), results
    assert set(results) == {
        "schema",
        "source_null",
        "category",
        "answer_type",
        "numbers",
        "mentions",
        "forbid",
        "no_warnings",
    }


def test_numbers_must_be_within_tolerance() -> None:
    case = _case(numbers=[{"label": "energy", "value": 239}, {"label": "fat", "value": 7.39}])
    outcome = Outcome(
        response=_response(answer="2 rotis have about 240 kcal and 8.1 g fat.", claims=[]),
        analysis_category="nutrition",
    )
    passed, detail = _results(case, outcome)["numbers"]
    assert not passed
    assert detail == "missing ['fat 7.39']"  # 240 is within 5% of 239; 8.1 is not of 7.39


def test_a_parse_failure_or_bad_response_fails_the_schema_check() -> None:
    case = _case()
    parse_failed = Outcome(
        response=_response(answer_type="error", claims=[]),
        analysis_category="nutrition",
        model_outputs=2,
        failures=[{"failure_type": "schema_validation_failed", "severity": "error"}],
    )
    assert _results(case, parse_failed)["schema"][0] is False
    assert parse_failed.parse_failures == 1

    malformed = Outcome(response=_response(extra="field"), analysis_category=None)
    assert _results(case, malformed)["schema"][0] is False


def test_source_null_and_warnings() -> None:
    outcome = Outcome(
        response=_response(claims=[{"text": "x", "source": "IFCT"}]),
        analysis_category="nutrition",
        failures=[{"failure_type": "unverified_number", "severity": "warning"}],
    )
    results = _results(_case(), outcome)
    assert results["source_null"] == (False, "1 non-null source(s)")
    assert results["no_warnings"] == (False, "unverified_number")


@pytest.mark.parametrize(
    ("blocked_by", "requests", "passed"),
    [
        ("input_gate", {}, True),
        ("input_gate", {"understanding": 1}, False),  # the model saw it
        ("classification", {"understanding": 1}, True),
        ("classification", {}, True),  # stopped even earlier
        ("classification", {"understanding": 1, "answer": 1}, False),
    ],
)
def test_blocked_in_code(blocked_by: str, requests: dict[str, int], passed: bool) -> None:
    outcome = Outcome(
        response=_response(answer_type="out_of_scope", category="out_of_scope", claims=[]),
        analysis_category=None,
        groq_requests=requests,
    )
    assert _results(_case(blocked_by=blocked_by), outcome)["blocked_in_code"][0] is passed


def test_an_answer_from_the_model_is_never_a_code_block() -> None:
    outcome = Outcome(
        response=_response(), analysis_category="out_of_scope", groq_requests={}, model_outputs=0
    )
    assert _results(_case(blocked_by="input_gate"), outcome)["blocked_in_code"][0] is False


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        ("**Not recommended — throw it away.** Rice left out overnight…", "discard"),
        ("### Food safety\n**No, don't eat it.** It was out too long.", "discard"),
        ("**Throw it away.**\n\nMilk spoils quickly.", "discard"),
        ("**Safe if eaten within 3 days** and reheated to 74 °C.", "conditional"),
        ("**Likely safe** — paneer keeps for 3 days in the fridge.", "safe"),
        ("**Yes, it is safe to eat** if it smells fine.", "safe"),
        ("Yes. Eggs keep for 3 weeks in the fridge.", "safe"),
        ("**Food safety**\n**Not safe in hot weather.** Discard it.", "discard"),
        ("**Safe if raw, not safe if cooked.**", "conditional"),
        ("**Not safe — throw it away, even if it looks fine.**", "discard"),
        ("Paneer is a good source of protein.", None),
    ],
)
def test_verdict_of(answer: str, verdict: str | None) -> None:
    assert verdict_of(answer)[0] == verdict


def test_verdict_must_be_at_the_start() -> None:
    answer = "Rice is a staple.\nIt has carbs.\nIt is eaten daily.\n**Throw it away.**"
    assert verdict_of(answer)[0] is None


def test_notices_and_mentions() -> None:
    case = _case(notices=[r"\b112\b"], mentions=["paneer", "dal"], mentions_any=["x", "protein"])
    outcome = Outcome(response=_response(), analysis_category="nutrition")
    results = _results(case, outcome)
    assert results["notices"] == (False, r"missing ['\\b112\\b']")
    assert results["mentions"] == (False, "missing ['dal']")


# --- Summary ---


def test_summary_metrics_and_targets() -> None:
    def result(case_id: str, checks: dict[str, bool], outputs: int = 2, parse: int = 0) -> Any:
        return {
            "id": case_id,
            "slice": "nutrition",
            "status": "passed" if all(checks.values()) else "failed",
            "checks": [{"name": n, "passed": p, "detail": ""} for n, p in checks.items()],
            "model_outputs": outputs,
            "parse_failures": parse,
            "failures": [],
            "latency_ms": 1000,
        }

    results = [
        result("a", {"schema": True, "source_null": True, "category": True, "numbers": True}),
        result("b", {"schema": True, "source_null": True, "category": False, "numbers": True}),
        {"id": "c", "slice": "scope", "status": "skipped", "reason": "limit"},
    ]
    summary = summarize(results, total_cases=3)

    metrics = summary["metrics"]
    assert metrics["schema_parse_rate"] == {"value": 1.0, "n": 4, "target": 1.0, "met": True}
    assert metrics["classification_accuracy"]["value"] == 0.5
    assert metrics["classification_accuracy"]["met"] is False
    assert metrics["food_safety_verdict_accuracy"]["value"] is None  # no verdict checks ran
    assert summary["cases_run"] == 2
    assert summary["complete"] is False
    assert summary["slices"]["scope"] == {"cases": 1, "passed": 0, "failed": 0, "skipped": 1}


def test_non_breaking_spaces_count_as_spaces() -> None:
    case = _case(mentions=[r"\b80 ?g"], forbid=[r"\b2 days\b"])
    outcome = Outcome(
        response=_response(answer="2 rotis (≈80\u202fg). Keep for 2\u00a0days.", claims=[]),
        analysis_category="nutrition",
    )
    results = _results(case, outcome)
    assert results["mentions"][0] is True
    assert results["forbid"][0] is False


def test_recheck_reruns_checks_on_stored_outcomes(tmp_path: Any) -> None:
    import json

    from tests.evals.run_evals import recheck

    case = next(c for c in CASES if c.id == "nut-paneer-protein")
    stored = {
        "id": case.id,
        "slice": case.slice,
        "question": case.question,
        "status": "failed",  # e.g. failed under a since-corrected check
        "checks": [],
        "response": _response(),
        "analysis": {"category": "nutrition"},
        "failures": [],
        "groq_requests": {"understanding": 1, "answer": 1},
        "model_outputs": 2,
        "parse_failures": 0,
        "latency_ms": 1000,
    }
    changed = {**stored, "id": "nut-roti-calories", "question": "Something else?"}
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"results": [stored, changed], "summary": {"cases_total": 2}}))

    assert recheck(path) == 0

    run = json.loads(path.read_text())
    first, second = run["results"]
    assert first["status"] == "passed"
    assert {c["name"] for c in first["checks"]} >= {"numbers", "category", "schema"}
    assert second["status"] == "skipped"
    assert "rechecked_at" in run
