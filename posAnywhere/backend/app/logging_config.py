"""Centralised logging configuration for the whole application.

`configure_logging()` is called once on startup (from app.main). Every module
obtains its logger via `logging.getLogger(__name__)` and inherits the format
and handlers set up here, so the entire app logs consistently:

  * a stream handler to stdout (captured by Render/Docker/cloud platforms)
  * an optional rotating file handler when LOG_FILE is configured

The format includes a UTC-ish timestamp, level, logger name and message.
"""

from __future__ import annotations

import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

# Per-request context, populated by the request-logging middleware. Because
# these are ContextVars, every log record emitted while handling a request
# (from any module) can be enriched with the same correlation id + user.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
user_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user", default="anonymous"
)

_TEXT_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | "
    "req=%(request_id)s user=%(user)s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Guard so repeated imports/calls don't attach duplicate handlers.
_configured = False


class ContextFilter(logging.Filter):
    """Inject the current request id + user onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user = user_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render each log record as a single-line JSON object for aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, _DATE_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user": getattr(record, "user", "anonymous"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Idempotently configure root logging from settings (level + optional file)."""
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    if settings.log_format.lower() == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(_TEXT_FORMAT, datefmt=_DATE_FORMAT)
    context_filter = ContextFilter()

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — always present so platform log collectors capture output.
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(context_filter)
    root.addHandler(console)

    # Optional rotating file handler for persistent, on-disk audit logs.
    if settings.log_file:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        root.addHandler(file_handler)

    # Route uvicorn's own loggers through the same level (handlers via root).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)

    _configured = True
    logging.getLogger(__name__).info(
        "Logging configured (level=%s, format=%s, file=%s)",
        settings.log_level.upper(),
        settings.log_format.lower(),
        settings.log_file or "<stdout only>",
    )
