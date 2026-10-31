"""Logging: a rotating file on disk, plus a thread-safe feed for the window's activity log.

Secrets registered with set_secret() are masked in every record, tracebacks included.
"""

from __future__ import annotations

import logging
import queue
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pydantic import BaseModel

LOGGER_NAME = "mediaforge"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_secrets: set[str] = set()
_installed: list[logging.Handler] = []


class LogLine(BaseModel):
    level: int
    text: str
    success: bool = False


def set_secret(value: str) -> None:
    value = value.strip()
    if len(value) >= 4:
        _secrets.add(value)


def _redact(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, "***")
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class UiLogBridge(logging.Handler):
    """Collects records from any thread; the UI drains them on its own loop."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self._queue: queue.SimpleQueue[LogLine] = queue.SimpleQueue()

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.put(
            LogLine(
                level=record.levelno,
                text=record.getMessage(),
                success=bool(getattr(record, "success", False)),
            )
        )

    def drain(self, max_items: int = 200) -> list[LogLine]:
        lines: list[LogLine] = []
        while len(lines) < max_items:
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return lines


def shutdown_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in _installed:
        logger.removeHandler(handler)
        handler.close()
    _installed.clear()


def configure_logging(logs_dir: Path) -> UiLogBridge:
    shutdown_logging()
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        logs_dir / "mediaforge.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    bridge = UiLogBridge()
    for handler in (file_handler, bridge):
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
        _installed.append(handler)
    return bridge


def log_success(logger: logging.Logger, msg: str) -> None:
    logger.info(msg, extra={"success": True})
