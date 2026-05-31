"""Centralised logging configuration for the whole application.

`configure_logging()` is called once on startup (from app.main). Every module
obtains its logger via `logging.getLogger(__name__)` and inherits the format
and handlers set up here, so the entire app logs consistently:

  * a stream handler to stdout (captured by Render/Docker/cloud platforms)
  * an optional rotating file handler when LOG_FILE is configured

The format includes a UTC-ish timestamp, level, logger name and message.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Guard so repeated imports/calls don't attach duplicate handlers.
_configured = False


def configure_logging() -> None:
    """Idempotently configure root logging from settings (level + optional file)."""
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — always present so platform log collectors capture output.
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # Optional rotating file handler for persistent, on-disk audit logs.
    if settings.log_file:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Route uvicorn's own loggers through the same level (handlers via root).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)

    _configured = True
    logging.getLogger(__name__).info(
        "Logging configured (level=%s, file=%s)",
        settings.log_level.upper(),
        settings.log_file or "<stdout only>",
    )
