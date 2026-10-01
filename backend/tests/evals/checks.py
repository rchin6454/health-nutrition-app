"""Deterministic eval checks: each compares one case's outcome with its expectations.

No model grades anything here. The optional LLM judge (`judge.py`) is reported separately and
never decides whether a case passes.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas.answer import ChatResponse
from tests.evals.cases import Case, Verdict

# Failure types meaning a model's output did not parse or broke a schema invariant.
PARSE_FAILURES = frozenset(
    {"schema_validation_failed", "source_not_null", "missing_clarifying_question"}
)
SOFT_WARNINGS = frozenset({"unverified_number", "unsupported_without_context"})
_UNICODE_SPACES = re.compile("[\u00a0\u2007\u2009\u202f]")


@dataclass
class Outcome:
    """What one case produced, as observed by the runner."""

    response: dict[str, Any]  # the ChatResponse, as returned by the API (JSON)
    analysis_category: str | None  # from the understanding call; None if it never ran
    groq_requests: dict[str, int] = field(default_factory=dict)  # requests that reached Groq
    model_outputs: int = 0  # calls that returned output to validate
    failures: list[dict[str, Any]] = field(default_factory=list)  # recorded failures

    @property
    def text(self) -> str:
        """The answer and every claim, for text checks. The model often puts a non-breaking or
        thin space between a number and its unit ("80\u202fg"); they are read as plain spaces."""
        claims = [str(c.get("text", "")) for c in self.response.get("claims", [])]
        return _UNICODE_SPACES.sub(" ", "\n".join([str(self.response.get("answer", "")), *claims]))

    @property
    def parse_failures(self) -> int:
        return sum(1 for f in self.failures if f["failure_type"] in PARSE_FAILURES)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def run_checks(case: Case, outcome: Outcome) -> list[CheckResult]:
    expect = case.expect
    results = [check_schema(outcome), check_source_null(outcome)]
    answer_type = outcome.response.get("answer_type")

    if expect.category is not None:
        got = outcome.analysis_category or outcome.response.get("category")
        results.append(
            CheckResult("category", got in expect.category, f"got {got}, want {expect.category}")
        )
    if expect.answer_type is not None:
        results.append(
            CheckResult(
                "answer_type",
                answer_type in expect.answer_type,
                f"got {answer_type}, want {expect.answer_type}",
            )
        )
    if expect.blocked_by is not None:
        results.append(check_blocked(expect.blocked_by, outcome))
    if expect.numbers:
        results.append(check_numbers(case, outcome))
    if expect.verdict is not None:
        results.append(check_verdict(expect.verdict, str(outcome.response.get("answer", ""))))
    if expect.mentions or expect.mentions_any:
        results.append(check_mentions(case, outcome))
    if expect.forbid:
        found = [p for p in expect.forbid if re.search(p, outcome.text, re.IGNORECASE)]
        results.append(CheckResult("forbid", not found, f"matched {found}" if found else ""))
    if expect.notices:
        notices = [str(n) for n in outcome.response.get("notices", [])]
        missing = [p for p in expect.notices if not any(re.search(p, n, re.I) for n in notices)]
        results.append(CheckResult("notices", not missing, f"missing {missing}" if missing else ""))
    if expect.no_warnings:
        warnings = sorted(
            {f["failure_type"] for f in outcome.failures if f["failure_type"] in SOFT_WARNINGS}
        )
        results.append(CheckResult("no_warnings", not warnings, ", ".join(warnings)))
    return results


def check_schema(outcome: Outcome) -> CheckResult:
    """The response parses as a ChatResponse, and every model output parsed (R2)."""
    try:
        ChatResponse.model_validate(outcome.response)
    except ValidationError as exc:
        return CheckResult("schema", False, f"response: {exc.error_count()} error(s)")
    if outcome.parse_failures:
        types = sorted({f["failure_type"] for f in outcome.failures})
        return CheckResult("schema", False, f"model output failed to parse: {types}")
    return CheckResult("schema", True)


def check_source_null(outcome: Outcome) -> CheckResult:
    sources = [c.get("source") for c in outcome.response.get("claims", [])]
    bad = [s for s in sources if s is not None]
    return CheckResult("source_null", not bad, f"{len(bad)} non-null source(s)" if bad else "")


def check_blocked(blocked_by: str, outcome: Outcome) -> CheckResult:
    """Blocked by a code gate: the input gate makes no Groq request at all; the classification
    gate stops after the understanding call, so the answer model is never called."""
    is_refusal = outcome.response.get("answer_type") == "out_of_scope"
    requests = outcome.groq_requests
    if blocked_by == "input_gate":
        passed = is_refusal and sum(requests.values()) == 0
    else:
        passed = is_refusal and requests.get("answer", 0) == 0
    return CheckResult(
        "blocked_in_code",
        passed,
        f"answer_type {outcome.response.get('answer_type')}, Groq requests {requests}",
    )


_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


def numbers_in(text: str) -> list[float]:
    return [float(n) for n in _NUMBER.findall(_THOUSANDS.sub("", text))]


def check_numbers(case: Case, outcome: Outcome) -> CheckResult:
    """Each expected reference value appears (rounded is fine) in the answer or a claim."""
    found = numbers_in(outcome.text)
    missing = [
        f"{n.label} {n.value:g}"
        for n in case.expect.numbers
        if not any(abs(x - n.value) <= n.tolerance * abs(n.value) for x in found)
    ]
    return CheckResult("numbers", not missing, f"missing {missing}" if missing else "")


_VERDICT_PATTERNS: list[tuple[Verdict, re.Pattern[str]]] = [
    (
        "discard",
        re.compile(
            r"^\W*no\b|\bnot\s+(?:recommended|safe|advisable)|\bunsafe\b|"
            r"\bthrow\s+(?:it\s+|them\s+)?(?:away|out)|\bdiscard|"
            r"\bdo\s+not\s+(?:eat|drink|use|refreeze)|\bdon'?t\s+(?:eat|drink|use|refreeze)|"
            r"\bavoid\b",
            re.IGNORECASE,
        ),
    ),
    (
        "conditional",
        re.compile(
            r"\bsafe\s+(?:if|only|when|as\s+long|for\s+up\s+to|for\s+\d|within)|\bonly\s+if\b|"
            r"\bdepends\b|\bwith\s+care\b|\bcaution\b|\bbe\s+careful\b|\bwithin\b",
            re.IGNORECASE,
        ),
    ),
    (
        "safe",
        re.compile(
            r"^\W*yes\b|\b(?:likely|probably|generally|usually|still)\s+safe\b|"
            r"\bis\s+safe\b|\bsafe\s+to\s+(?:eat|drink|use)|^\W*safe\b",
            re.IGNORECASE,
        ),
    ),
]
VERDICT_LINES = 3  # the verdict must be in the first few lines of text (headings skipped)


def verdict_of(answer: str) -> tuple[Verdict | None, str]:
    """The verdict stated at the start of an answer, and the text it was read from.

    Reads the first bold phrase (or else the first sentence) of each of the first few non-heading
    lines, and returns the verdict the first such phrase leads with.
    """
    lines = [
        line.strip()
        for line in answer.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in lines[:VERDICT_LINES]:
        line = re.sub(r"^(?:[-*+>]|\d+\.)\s+", "", line)
        bold = re.match(r"\*\*(.+?)\*\*", line)
        lead = bold.group(1) if bold else re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
        # The verdict is the one the phrase leads with: "Safe if raw, not safe if cooked" is
        # conditional. On a tie the more cautious verdict (listed first) wins.
        found = [
            (match.start(), rank, verdict)
            for rank, (verdict, pattern) in enumerate(_VERDICT_PATTERNS)
            if (match := pattern.search(lead))
        ]
        if found:
            return min(found)[2], lead
    return None, lines[0] if lines else ""


def check_verdict(expected: list[Verdict], answer: str) -> CheckResult:
    verdict, lead = verdict_of(answer)
    return CheckResult(
        "verdict", verdict in expected, f"got {verdict} from {lead[:80]!r}, want {expected}"
    )


def check_mentions(case: Case, outcome: Outcome) -> CheckResult:
    text = outcome.text
    missing = [p for p in case.expect.mentions if not re.search(p, text, re.IGNORECASE)]
    any_ok = not case.expect.mentions_any or any(
        re.search(p, text, re.IGNORECASE) for p in case.expect.mentions_any
    )
    problems = [f"missing {missing}"] if missing else []
    if not any_ok:
        problems.append(f"none of {case.expect.mentions_any}")
    return CheckResult("mentions", not problems, "; ".join(problems))
