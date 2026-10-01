"""Structured JSON logging: one JSON object per line on stderr, for Railway's log viewer.

`request_id` and `conversation_id` are set per chat request (`bind_request`) and added to every
log line written while that request runs, including lines from other modules. Extra fields
passed as `logger.info(..., extra={"fields": {...}})` are merged into the JSON object.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_conversation_id: ContextVar[str | None] = ContextVar("conversation_id", default=None)


def bind_request(request_id: str, conversation_id: str | None) -> None:
    """Tag every log line of the current request (asyncio task) with these ids."""
    _request_id.set(request_id)
    _conversation_id.set(conversation_id)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = _request_id.get()
        if request_id:
            entry["request_id"] = request_id
        conversation_id = _conversation_id.get()
        if conversation_id:
            entry["conversation_id"] = conversation_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Send the app's and uvicorn's logs through one JSON handler."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # The access middleware in app.main logs each request as JSON instead.
    logging.getLogger("uvicorn.access").disabled = True
