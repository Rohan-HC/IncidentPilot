"""Small, local-first structured logging and operational counters."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from threading import Lock
from typing import Any, Iterator


correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
investigation_id_var: ContextVar[str | None] = ContextVar("investigation_id", default=None)

_SENSITIVE_FIELDS = {
    "authorization", "password", "token", "access_token", "secret", "prompt", "runbook_text"
}


@contextmanager
def bind_context(
    *, correlation_id: str | None = None, investigation_id: str | None = None
) -> Iterator[None]:
    tokens = []
    if correlation_id is not None:
        tokens.append((correlation_id_var, correlation_id_var.set(correlation_id)))
    if investigation_id is not None:
        tokens.append((investigation_id_var, investigation_id_var.set(investigation_id)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        correlation_id = correlation_id_var.get()
        investigation_id = investigation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if investigation_id:
            payload["investigation_id"] = investigation_id
        payload.update(getattr(record, "observability", {}))
        return json.dumps(payload, separators=(",", ":"), default=str)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    safe = {key: value for key, value in fields.items() if key.lower() not in _SENSITIVE_FIELDS}
    logger.info(event, extra={"event": event, "observability": safe})


def configure_logging() -> None:
    logger = logging.getLogger("incidentpilot")
    if not any(getattr(handler, "_incidentpilot_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._incidentpilot_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class CounterRegistry:
    def __init__(self) -> None:
        self._values: Counter[str] = Counter()
        self._lock = Lock()

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._values[name] += value

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)


COUNTERS = CounterRegistry()
