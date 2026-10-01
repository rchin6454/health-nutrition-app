"""Gate 1: runs on the raw message before any model call (architecture §7).

Pure: returns a decision; the orchestrator records it and builds the response.
"""

from dataclasses import dataclass

from app.scope.blocked_topics import find_blocked_topic, find_injection, is_emergency

MIN_CHARS = 2
MAX_CHARS = 1000

TOO_SHORT_REPLY = "Please ask a full question about food, nutrition or food safety."
TOO_LONG_REPLY = f"Please ask a shorter question (up to {MAX_CHARS:,} characters)."


@dataclass(frozen=True)
class Rejection:
    reason: str  # recorded as the `failure_type` of the scope_block row
    reply: str  # the code-written answer text
    detail: str


@dataclass(frozen=True)
class InputGateResult:
    rejection: Rejection | None  # set → no model call is made
    emergency: bool  # add the 112/108 notice to whatever response is returned
    injection: str | None  # suspicious text; recorded, and the request continues


def check_input(message: str) -> InputGateResult:
    text = message.strip()
    emergency = is_emergency(text)
    injection = find_injection(text)

    rejection: Rejection | None = None
    if len(text) < MIN_CHARS:
        rejection = Rejection("message_too_short", TOO_SHORT_REPLY, f"{len(text)} chars")
    elif len(text) > MAX_CHARS:
        rejection = Rejection("message_too_long", TOO_LONG_REPLY, f"{len(text)} chars")
    elif match := find_blocked_topic(text):
        rejection = Rejection(match.topic.id, match.topic.referral, f"matched: {match.matched!r}")

    return InputGateResult(rejection=rejection, emergency=emergency, injection=injection)
