"""Validation of model output (architecture §6.4). Checks and reports; never repairs."""

from dataclasses import dataclass

from pydantic import ValidationError

from app.schemas.answer import LLMAnswer

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
