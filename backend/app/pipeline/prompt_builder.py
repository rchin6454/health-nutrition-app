"""Builds the message lists for both model calls (architecture §5.5).

User text sits inside tagged blocks that the system prompts tell the model to treat as data.
"""

import json

from app.llm.client import ChatMessage
from app.prompts import ANSWER_SYSTEM_PROMPT, UNDERSTANDING_SYSTEM_PROMPT
from app.schemas.analysis import QuestionAnalysis
from app.schemas.context import ContextBundle
from app.store.conversations import StoredMessage

HISTORY_TURNS = 6

EMPTY_CONTEXT = "(No verified reference data was found for this question.)"


def history_messages(history: list[StoredMessage]) -> list[ChatMessage]:
    """Recent turns: user text, and only the `answer` text of earlier non-error replies."""
    messages: list[ChatMessage] = []
    for msg in history:
        if msg.role == "user":
            messages.append({"role": "user", "content": str(msg.content.get("text", ""))})
        elif msg.content.get("answer_type") != "error":
            messages.append({"role": "assistant", "content": str(msg.content.get("answer", ""))})
    return messages


def user_question_block(question: str) -> str:
    return f"<user_question>\n{question}\n</user_question>"


def build_understanding_messages(question: str, history: list[StoredMessage]) -> list[ChatMessage]:
    return [
        {"role": "system", "content": UNDERSTANDING_SYSTEM_PROMPT},
        *history_messages(history),
        {"role": "user", "content": user_question_block(question)},
    ]


def build_answer_messages(
    question: str,
    history: list[StoredMessage],
    analysis: QuestionAnalysis,
    context: ContextBundle | None = None,
) -> list[ChatMessage]:
    final = "\n\n".join(
        [
            f"<question_analysis>\n{format_analysis(analysis)}\n</question_analysis>",
            f"<context>\n{format_context(context)}\n</context>",
            user_question_block(question),
        ]
    )
    return [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        *history_messages(history),
        {"role": "user", "content": final},
    ]


def format_analysis(analysis: QuestionAnalysis) -> str:
    """The parts of the analysis the answer model needs, one `key: value` per line."""
    entities = analysis.entities
    fields = {
        "category": analysis.category,
        "question_type": analysis.question_type,
        "intent": analysis.intent_summary,
        "foods": entities.foods,
        "nutrients": entities.nutrients,
        "quantities": [q.model_dump() for q in entities.quantities],
        "storage": entities.storage.model_dump() if entities.storage else None,
        "cooking_methods": entities.cooking_methods,
        "user_context": analysis.user_context,
        "risk_flags": analysis.risk_flags,
    }
    return "\n".join(
        f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"
        for key, value in fields.items()
    )


def format_context(context: ContextBundle | None) -> str:
    """Facts as `[F1] …` lines, then the assumptions and the foods with no verified data.

    Dataset names (`Fact.origin`) are left out: they are for the audit trail only.
    """
    if context is None:
        return EMPTY_CONTEXT
    lines = [f"[{f.id}] {f.content}" for f in context.facts]
    lines += [f"[{p.id}] {p.text}" for p in context.passages]
    if not lines:
        lines.append(EMPTY_CONTEXT)
    if context.assumptions:
        lines.append("Assumptions:")
        lines += [f"- {a}" for a in context.assumptions]
    if context.unresolved_entities:
        lines.append("No verified data found for: " + ", ".join(context.unresolved_entities))
    return "\n".join(lines)
