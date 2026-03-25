"""Helpers for OpenAI strict JSON schemas."""

from __future__ import annotations

from typing import Any

from openai.lib._pydantic import to_strict_json_schema


def build_strict_openai_schema(model: type[Any]) -> dict[str, Any]:
    """Return an OpenAI strict-mode compatible JSON schema for a Pydantic model."""
    return to_strict_json_schema(model)
