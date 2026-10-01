"""Collects the knowledge modules' facts, passages and assumptions into one `ContextBundle`."""

from app.schemas.context import ContextBundle, Fact, FactKind, Passage

DATASET_ORIGINS = {"IFCT2017": "IFCT 2017 (ICMR-NIN)", "USDA_FDC": "USDA FoodData Central"}


class ContextBuilder:
    def __init__(self) -> None:
        self._facts: list[Fact] = []
        self._passages: list[Passage] = []
        self._assumptions: list[str] = []
        self._unresolved: list[str] = []

    def add_fact(self, kind: FactKind, content: str, origin: str) -> None:
        self._facts.append(
            Fact(id=f"F{len(self._facts) + 1}", kind=kind, content=content, origin=origin)
        )

    def add_passage(self, text: str, origin: str, score: float) -> None:
        self._passages.append(
            Passage(id=f"P{len(self._passages) + 1}", text=text, origin=origin, score=score)
        )

    def assume(self, text: str) -> None:
        if text not in self._assumptions:
            self._assumptions.append(text)

    def unresolved(self, name: str) -> None:
        if name not in self._unresolved:
            self._unresolved.append(name)

    def build(self) -> ContextBundle:
        return ContextBundle(
            facts=list(self._facts),
            passages=list(self._passages),
            unresolved_entities=list(self._unresolved),
            assumptions=list(self._assumptions),
        )
