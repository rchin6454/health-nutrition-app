"""Builds the `ContextBundle` for an in-scope question (architecture §5.4).

- nutrition, general_food: nutrition lookup + passages
- food_safety: safety rules + passages
- mixed: safety rules, then nutrition lookup, then passages

Each step runs on its own: if one fails (database error, embedding model not loaded) the error
is returned for the orchestrator to record, and the facts from the other steps are kept.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.knowledge.builder import ContextBuilder
from app.knowledge.nutrition import add_nutrition_context
from app.knowledge.retriever import add_passages
from app.knowledge.safety import add_safety_context
from app.schemas.analysis import Category, QuestionAnalysis
from app.schemas.context import ContextBundle

NUTRITION_CATEGORIES: frozenset[Category] = frozenset({"nutrition", "general_food", "mixed"})
SAFETY_CATEGORIES: frozenset[Category] = frozenset({"food_safety", "mixed"})
KNOWLEDGE_CATEGORIES = NUTRITION_CATEGORIES | SAFETY_CATEGORIES


@dataclass(frozen=True)
class KnowledgeError:
    failure_type: str  # knowledge_lookup_failed | safety_lookup_failed | retrieval_failed
    detail: str


@dataclass(frozen=True)
class KnowledgeResult:
    context: ContextBundle
    errors: list[KnowledgeError]


def uses_knowledge(analysis: QuestionAnalysis) -> bool:
    return analysis.category in KNOWLEDGE_CATEGORIES


async def build_context(analysis: QuestionAnalysis, question: str) -> KnowledgeResult:
    builder = ContextBuilder()
    steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    if analysis.category in SAFETY_CATEGORIES:
        steps.append(
            ("safety_lookup_failed", lambda: add_safety_context(builder, analysis, question))
        )
    if analysis.category in NUTRITION_CATEGORIES:
        steps.append(("knowledge_lookup_failed", lambda: add_nutrition_context(builder, analysis)))
    steps.append(("retrieval_failed", lambda: add_passages(builder, analysis)))

    errors: list[KnowledgeError] = []
    for failure_type, step in steps:
        try:
            await step()
        except Exception as exc:
            errors.append(KnowledgeError(failure_type, repr(exc)))
    return KnowledgeResult(builder.build(), errors)
