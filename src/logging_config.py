"""Logging configuration for PictoAgent."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .logging_context import get_log_context

_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys()) | {
    "message",
    "asctime",
    "process_id",
    "user_id",
    "telegram_user_id",
    "update_id",
    "action",
    "workflow",
}


class ActionContextFilter(logging.Filter):
    """Inject request-scoped trace metadata into each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = get_log_context()
        record.process_id = context.get("process_id", "-")
        record.user_id = context.get("user_id", "-")
        record.telegram_user_id = context.get("telegram_user_id", "-")
        record.update_id = context.get("update_id", "-")
        record.action = context.get("action", "-")
        record.workflow = context.get("workflow", "-")
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as one-line JSON for easier filtering."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process_id": getattr(record, "process_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "telegram_user_id": getattr(record, "telegram_user_id", "-"),
            "update_id": getattr(record, "update_id", "-"),
            "action": getattr(record, "action", "-"),
            "workflow": getattr(record, "workflow", "-"),
            "os_pid": record.process,
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = _serialize_value(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    return str(value)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure structured logging for the application."""
    formatter = JsonFormatter()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.addFilter(ActionContextFilter())
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
