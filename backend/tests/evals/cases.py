"""The eval case format (`cases.yaml`) and its loader.

A case is one question plus what a correct response must show. Every expectation is checked
in code (`checks.py`); none needs a model to grade it.
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.analysis import Category
from app.schemas.answer import AnswerType

CASES_FILE = Path(__file__).with_name("cases.yaml")

Slice = Literal[
    "classification",
    "nutrition",
    "food_safety",
    "clarification",
    "scope",
    "indian_context",
    "uncertainty",
]
Verdict = Literal["discard", "safe", "conditional"]
BlockedBy = Literal["input_gate", "classification"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Turn(Model):
    """An earlier turn of the conversation, for follow-up questions."""

    user: str
    assistant: str


class ExpectedNumber(Model):
    """A value that must appear in the answer or claims, within ±`tolerance` of `value`."""

    label: str  # what it is, for reports: "protein per 100 g paneer"
    value: float
    tolerance: float = 0.05  # relative


class Expect(Model):
    category: list[Category] | None = None  # any of these
    answer_type: list[AnswerType] | None = None  # any of these
    blocked_by: BlockedBy | None = None
    numbers: list[ExpectedNumber] = []
    verdict: list[Verdict] | None = None  # the verdict at the start of the answer: any of these
    mentions: list[str] = []  # regexes; each must match the answer or a claim
    mentions_any: list[str] = []  # regexes; at least one must match
    forbid: list[str] = []  # regexes; none may match
    notices: list[str] = []  # regexes; each must match one of the notices
    # Soft-check warnings (unverified_number, unsupported_without_context) fail the case.
    no_warnings: bool = True

    @field_validator("category", "answer_type", "verdict", mode="before")
    @classmethod
    def _one_or_many(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value


class Case(Model):
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
    slice: Slice
    question: str
    history: list[Turn] = []
    smoke: bool = False  # part of the small subset CI runs on prompt/schema/scope changes
    note: str | None = None  # why the case exists (e.g. a production failure it reproduces)
    expect: Expect


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cases = [Case.model_validate(item) for item in raw]
    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate case ids: {duplicates}")
    return cases
