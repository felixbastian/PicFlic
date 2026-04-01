"""Per-user Telegram state helpers."""

from __future__ import annotations

from typing import Any, Mapping

from telegram.ext import ContextTypes

from .constants import LAST_NUTRITION_RESULT_KEY, RECENT_HISTORY_KEY, RECENT_HISTORY_LIMIT


def get_recent_history(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    """Return the recent text conversation history stored for the current Telegram user."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return []

    history = user_data.get(RECENT_HISTORY_KEY, [])
    if not isinstance(history, list):
        return []

    recent_items: list[dict[str, str]] = []
    for item in history[-RECENT_HISTORY_LIMIT:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        text = str(item.get("text") or "").strip()
        if not role or not text:
            continue
        normalized_item = {"role": role, "text": text}
        workflow = str(item.get("workflow") or "").strip()
        if workflow:
            normalized_item["workflow"] = workflow
        recent_items.append(normalized_item)
    return recent_items


def remember_text_turn(
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    assistant_messages: list[str],
    workflow_type: str,
) -> None:
    """Store the latest text turn so the orchestrator can use short-term chat history."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return

    history = get_recent_history(context)
    normalized_user_text = user_text.strip()
    if normalized_user_text:
        history.append({"role": "user", "text": normalized_user_text, "workflow": workflow_type})
    for assistant_message in assistant_messages:
        normalized_message = assistant_message.strip()
        if normalized_message:
            history.append({"role": "assistant", "text": normalized_message, "workflow": workflow_type})
    user_data[RECENT_HISTORY_KEY] = history[-RECENT_HISTORY_LIMIT:]


def remember_latest_nutrition_result(context: ContextTypes.DEFAULT_TYPE, result: Mapping[str, Any]) -> None:
    """Store the latest nutrition result so a follow-up text can correct it."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return

    analysis = result.get("analysis")
    if not isinstance(analysis, dict):
        return

    payload = {
        "record_id": str(result.get("record_id") or "").strip(),
        "meal_id": str(result.get("meal_id") or "").strip(),
        "analysis": analysis,
    }
    user_data[LAST_NUTRITION_RESULT_KEY] = payload


def get_latest_nutrition_result(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    """Return the last nutrition result stored for follow-up corrections."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return None

    payload = user_data.get(LAST_NUTRITION_RESULT_KEY)
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("analysis"), dict):
        return None
    return payload


def clear_latest_nutrition_result(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove any pending nutrition correction context."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return
    user_data.pop(LAST_NUTRITION_RESULT_KEY, None)
