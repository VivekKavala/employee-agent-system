"""Shared structured logging setup with secret scrubbing and rotating file output."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"gsk_[A-Za-z0-9_\-]{12,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(?:api[_-]?key|authorization|bearer)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}['\"]?"),
]


def scrub_sensitive_text(value: str) -> str:
    """Redact potential API keys or authorization tokens from log strings."""
    if not value:
        return value
    cleaned = value
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
    return cleaned


class StructuredLogFormatter(logging.Formatter):
    """Formats log records as single-line structured JSON with consistent operational fields."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "conversation_id": getattr(record, "conversation_id", "-"),
            "agent_name": getattr(record, "agent_name", "system"),
            "operation": getattr(record, "operation", "general"),
            "status": getattr(record, "status", "info"),
            "duration_ms": getattr(record, "duration_ms", None),
            "error_category": getattr(record, "error_category", None),
            "message": scrub_sensitive_text(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = scrub_sensitive_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    log_level: str = "INFO",
    log_file_path: Optional[Path] = None,
    console_output: bool = False,
) -> logging.Logger:
    """Configure and return the shared application logger."""
    logger = logging.getLogger("employee_agent_system")
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = True

    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    formatter = StructuredLogFormatter()

    if log_file_path is not None:
        log_file_path = Path(log_file_path)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=str(log_file_path),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if console_output:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if not logger.handlers:
        null_handler = logging.NullHandler()
        logger.addHandler(null_handler)

    return logger


def get_logger() -> logging.Logger:
    """Retrieve the shared application logger."""
    return logging.getLogger("employee_agent_system")


def log_event(
    level: int,
    message: str,
    *,
    conversation_id: str = "-",
    agent_name: str = "system",
    operation: str = "general",
    status: str = "info",
    duration_ms: Optional[float] = None,
    error_category: Optional[str] = None,
) -> None:
    """Emit a structured operational log entry without leaking sensitive fields."""
    logger = get_logger()
    extra = {
        "conversation_id": conversation_id or "-",
        "agent_name": agent_name,
        "operation": operation,
        "status": status,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "error_category": error_category,
    }
    logger.log(level, scrub_sensitive_text(message), extra=extra)
