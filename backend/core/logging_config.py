"""Structured logging. Sensitive content (reports, tokens, passwords) is never logged."""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from backend.core.config import get_settings

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id: str | None = None) -> str:
    rid = request_id or uuid.uuid4().hex[:16]
    _request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id_var.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id_var.get(),
        }
        for key in ("event", "duration_ms", "user_id", "report_id", "provider", "status"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<8} "
            f"[{_request_id_var.get()}] {record.name}: {record.getMessage()}"
        )
        extra_keys = ("event", "duration_ms", "user_id", "report_id", "provider", "status")
        extras = " ".join(
            f"{key}={getattr(record, key)}"
            for key in extra_keys
            if getattr(record, key, None) is not None
        )
        if extras:
            base += f" | {extras}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class TimingLog:
    """Context manager that logs an operation's duration (observability)."""

    def __init__(
        self,
        logger: logging.Logger,
        event: str,
        level: int = logging.INFO,
        **fields: Any,
    ) -> None:
        self._logger = logger
        self._event = event
        self._level = level
        self._fields = fields
        self._start = 0.0

    def __enter__(self) -> "TimingLog":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration_ms = int((time.perf_counter() - self._start) * 1000)
        fields = {**self._fields, "event": self._event, "duration_ms": duration_ms}
        if exc is not None:
            self._logger.log(
                logging.ERROR,
                "%s failed after %dms", self._event, duration_ms, extra=fields
            )
        else:
            self._logger.log(self._level, "%s completed in %dms", self._event, duration_ms, extra=fields)


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_json else TextFormatter())
    root.addHandler(handler)
    root.setLevel(settings.logging_level.upper())

    # Quiet noisy third-party loggers.
    for name in ("uvicorn.access", "sqlalchemy.engine", "httpx", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)
