"""System prompts. Every prompt change bumps its version (saved on messages and failures)."""

from pathlib import Path

_DIR = Path(__file__).parent

ANSWER_PROMPT_VERSION = "answer-v0.5"
UNDERSTANDING_PROMPT_VERSION = "understanding-v0.5"

# The pair of prompts a turn ran with; stored on assistant messages.
PROMPT_VERSION = f"{ANSWER_PROMPT_VERSION}+{UNDERSTANDING_PROMPT_VERSION}"

ANSWER_SYSTEM_PROMPT = (_DIR / "answer.md").read_text(encoding="utf-8")
UNDERSTANDING_SYSTEM_PROMPT = (_DIR / "understanding.md").read_text(encoding="utf-8")
