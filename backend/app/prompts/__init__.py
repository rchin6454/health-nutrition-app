"""System prompts. Every prompt change bumps its version (saved on messages and failures)."""

from pathlib import Path

_DIR = Path(__file__).parent

PROMPT_VERSION = "answer-v0.1"

ANSWER_SYSTEM_PROMPT = (_DIR / "answer.md").read_text(encoding="utf-8")
