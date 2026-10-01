"""Validation of model output (architecture §6.4). Checks and reports; never repairs."""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.schemas.answer import LLMAnswer
from app.schemas.context import ContextBundle

MAX_ANSWER_CHARS = 1500
MAX_CLAIMS = 8
MAX_CLAIM_CHARS = 300


@dataclass(frozen=True)
class ValidationFailure:
    failure_type: str  # schema_validation_failed | source_not_null | empty_answer | ...
    detail: str


def validate_llm_output(raw: str | None) -> LLMAnswer | ValidationFailure:
    """Parse, then run the hard invariants. Returns the answer or the first failure."""
    parsed = parse_llm_answer(raw)
    if isinstance(parsed, ValidationFailure):
        return parsed
    return check_invariants(parsed) or parsed


def parse_llm_answer(raw: str | None) -> LLMAnswer | ValidationFailure:
    """Parse the raw model output exactly as returned. No JSON repair."""
    if raw is None:
        return ValidationFailure("schema_validation_failed", "model returned no content")
    try:
        return LLMAnswer.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        # A non-null `source` is reported as its own failure type so R4 violations stand out.
        if any(_is_source_error(e["loc"]) for e in errors):
            return ValidationFailure("source_not_null", str(exc))
        return ValidationFailure("schema_validation_failed", str(exc))


def _is_source_error(loc: tuple[int | str, ...]) -> bool:
    return len(loc) == 3 and loc[0] == "claims" and loc[2] == "source"


def check_invariants(answer: LLMAnswer) -> ValidationFailure | None:
    """Hard invariants the schema cannot express. Returns the first violation, if any."""
    for i, claim in enumerate(answer.claims):
        if claim.source is not None:  # guaranteed by the type; re-checked for R4
            return ValidationFailure("source_not_null", f"claims[{i}].source is not null")
    if not answer.answer.strip():
        return ValidationFailure("empty_answer", "answer text is empty")
    if not answer.claims:
        return ValidationFailure("empty_claims", "claims list is empty")
    if len(answer.answer) > MAX_ANSWER_CHARS:
        return ValidationFailure(
            "length_exceeded", f"answer has {len(answer.answer)} chars (max {MAX_ANSWER_CHARS})"
        )
    if len(answer.claims) > MAX_CLAIMS:
        return ValidationFailure(
            "length_exceeded", f"{len(answer.claims)} claims (max {MAX_CLAIMS})"
        )
    for i, claim in enumerate(answer.claims):
        if not claim.text.strip():
            return ValidationFailure("empty_claims", f"claims[{i}].text is empty")
        if len(claim.text) > MAX_CLAIM_CHARS:
            return ValidationFailure(
                "length_exceeded",
                f"claims[{i}] has {len(claim.text)} chars (max {MAX_CLAIM_CHARS})",
            )
    return None


# --- Soft checks: recorded as warnings; the response is returned unchanged ---

_NUMBER = r"\d+(?:\.\d+)?"
# A number followed by a nutrient unit: "18.9 g", "258 kcal", "3mg". Storage times and
# temperatures ("2 hours", "32 °C") are not nutrient numbers.
_NUTRIENT_NUMBER = re.compile(
    rf"({_NUMBER})\s*(?:g|gm|gms|grams?|mg|mcg|µg|ug|kcal|calories|cal|kj)\b", re.IGNORECASE
)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
# Rounding the model may do to a fact's value: 18.86 → 18.9 or 19, 2.95 → 3.
_RELATIVE_TOLERANCE = 0.02
_ABSOLUTE_TOLERANCE = 0.051


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(_NUMBER, _THOUSANDS.sub("", text))]


def unverified_numbers(answer: LLMAnswer, context: ContextBundle, question: str) -> list[str]:
    """Nutrient numbers in claims that match no number in the context or the question.

    Returns one "claims[i]: <number with unit>" entry per unverified number.
    """
    known = _numbers(question)
    for fact in context.facts:
        known += _numbers(fact.content)
    for passage in context.passages:
        known += _numbers(passage.text)
    for text in context.assumptions:
        known += _numbers(text)

    def verified(value: float) -> bool:
        return any(
            abs(value - k) <= max(_RELATIVE_TOLERANCE * abs(k), _ABSOLUTE_TOLERANCE) for k in known
        )

    found = []
    for i, claim in enumerate(answer.claims):
        text = _THOUSANDS.sub("", claim.text)
        for match in _NUTRIENT_NUMBER.finditer(text):
            if _is_basis(text, match.start()):
                continue
            if not verified(float(match.group(1))):
                found.append(f"claims[{i}]: {match.group(0)}")
    return found


def _is_basis(text: str, start: int) -> bool:
    """ "per 100 g" is the reference amount a value is given for, not a nutrient number."""
    return text[:start].rstrip().lower().endswith("per")


# Wording that tells the user the answer is not backed by verified reference data. The answer
# prompt asks for one such sentence whenever <context> is empty.
_HEDGE = re.compile(
    r"could\s*n[o']t\s+(?:be\s+)?(?:verif|check|confirm|find)|"
    r"can\s*n[o']t\s+(?:be\s+)?(?:verif|check|confirm)|"
    r"(?:not|un)\s*(?:been\s+)?(?:verified|checked|confirmed)|unverified|"
    r"no\s+verified|not\s+able\s+to\s+(?:verify|check|confirm)|"
    r"unable\s+to\s+(?:verify|check|confirm)|approximate",
    re.IGNORECASE,
)


def unsupported_without_context(answer: LLMAnswer, context: ContextBundle | None) -> str | None:
    """With no facts or passages, the answer must say it could not be verified (§6.4).

    Returns a description of the problem, or None when the context was not empty or the answer
    hedges.
    """
    if context is not None and not context.is_empty:
        return None
    if _HEDGE.search(answer.answer):
        return None
    return (
        f"no verified context, and the answer states {len(answer.claims)} claim(s) without "
        "saying they could not be verified"
    )
