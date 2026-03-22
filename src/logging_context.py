"""Request-scoped logging context helpers."""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


_LOG_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "picflic_log_context",
    default={},
)


def generate_process_id(prefix: str = "act") -> str:
    """Generate a short process id for tracing a single user action."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def get_log_context() -> dict[str, str]:
    """Return the current request-scoped logging context."""
    return dict(_LOG_CONTEXT.get())


def bind_log_context(**values: Any) -> contextvars.Token[dict[str, str]]:
    """Merge values into the current logging context."""
    current = get_log_context()
    for key, value in values.items():
        if value is None:
            continue
        current[key] = str(value)
    return _LOG_CONTEXT.set(current)


def reset_log_context(token: contextvars.Token[dict[str, str]]) -> None:
    """Reset the logging context to a previous token."""
    _LOG_CONTEXT.reset(token)


def clear_log_context() -> None:
    """Clear any bound logging context."""
    _LOG_CONTEXT.set({})


@contextmanager
def logging_context(**values: Any) -> Iterator[dict[str, str]]:
    """Temporarily bind values to the request-scoped logging context."""
    token = bind_log_context(**values)
    try:
        yield get_log_context()
    finally:
        reset_log_context(token)
