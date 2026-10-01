"""Context given to the answer model and saved for audit (architecture §5.4).

Facts and passages are context for the model only. `origin` names the dataset for the
`context_snapshot` audit trail; it is never shown to the model or copied into claims (R4).
"""

from typing import Literal

from app.schemas.answer import Strict

FactKind = Literal["nutrient", "comparison", "recommendation", "safety_rule"]


class Fact(Strict):
    id: str  # "F1"
    kind: FactKind
    content: str  # "Paneer (raw): per 100 g, protein 18.9 g, energy 258 kcal"
    origin: str  # "IFCT 2017", kept for audit only


class Passage(Strict):
    id: str  # "P1"
    text: str
    origin: str
    score: float


class ContextBundle(Strict):
    facts: list[Fact]
    passages: list[Passage]
    unresolved_entities: list[str]
    assumptions: list[str]

    @property
    def is_empty(self) -> bool:
        return not self.facts and not self.passages


EMPTY_BUNDLE = ContextBundle(facts=[], passages=[], unresolved_entities=[], assumptions=[])
