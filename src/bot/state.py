"""Per-user Telegram state helpers."""

from __future__ import annotations

from typing import Any, Mapping

from telegram.ext import ContextTypes

from .constants import (
    LAST_EXPENSE_RESULT_KEY,
    LAST_NUTRITION_RESULT_KEY,
    LAST_TRACKING_RESULT_KEY,
    RECENT_HISTORY_KEY,
    RECENT_HISTORY_LIMIT,
)

def get_recent_history(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    """Return the user's recent text history in a normalized agent-facing format.

    Input:
    - reads ``context.user_data[RECENT_HISTORY_KEY]``, which may contain raw
      persisted items of mixed quality.

    Output:
    - returns a list of dicts like ``{"role": "user", "text": "..."}``
      and, when available, ``{"workflow": "nutrition_tracking"}``;
    - includes at most the last ``RECENT_HISTORY_LIMIT`` valid items;
    - returns ``[]`` when no usable history is stored.
    """
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return []

    history = _get_raw_recent_history(user_data)

    recent_items: list[dict[str, str]] = []
    for item in history[-RECENT_HISTORY_LIMIT:]:
        normalized_item = _normalize_recent_history_item(item)
        if normalized_item is not None:
            recent_items.append(normalized_item)
    return recent_items



def _get_user_data_dict(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    """Return ``context.user_data`` only when it is a dict-like Telegram state store."""
    user_data = getattr(context, "user_data", None)
    if not isinstance(user_data, dict):
        return None
    return user_data


def _get_raw_recent_history(user_data: Mapping[str, Any]) -> list[Any]:
    """Return the stored recent-history payload when it is a list, otherwise ``[]``."""
    history = user_data.get(RECENT_HISTORY_KEY, [])
    if not isinstance(history, list):
        return []
    return history


def _normalize_recent_history_item(item: Any) -> dict[str, str] | None:
    """Validate and clean one stored history item before it reaches the agent.

    ``context.user_data`` is loosely typed, so this helper acts as the boundary
    between raw persisted state and the compact, predictable shape expected by
    ``get_recent_history()``. Invalid or empty items are rejected by returning
    ``None``; valid items are normalized into ``{"role": ..., "text": ...}``
    plus ``workflow`` when present.
    """
    if not isinstance(item, dict):
        return None

    role = str(item.get("role") or "").strip()
    text = str(item.get("text") or "").strip()
    if not role or not text:
        return None

    normalized_item = {"role": role, "text": text}
    workflow = str(item.get("workflow") or "").strip()
    if workflow:
        normalized_item["workflow"] = workflow
    return normalized_item




def remember_text_turn(
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
    assistant_messages: list[str],
    workflow_type: str,
) -> None:
    """Append one text interaction to the compact recent-history window.

    This stores the current user message and the assistant replies as plain text
    items under ``RECENT_HISTORY_KEY``. The goal is to help later text messages
    feel contextual, for example when the user asks a follow-up question like
    "can you explain that" or "delete the last one".

    A few design choices matter here:
    - the function first calls :func:`get_recent_history`, so it always rebuilds
      history from a cleaned, normalized version of whatever is currently stored;
    - the user message is stored first, then each assistant reply in order, which
      preserves the conversational turn structure;
    - every stored item includes ``workflow`` so later routing can distinguish
      whether a message came from nutrition tracking, vocabulary, deletions, and
      so on;
    - blank strings are ignored to avoid wasting the tiny history budget;
    - after appending, history is trimmed to ``RECENT_HISTORY_LIMIT`` so we keep
      only a short sliding window instead of an ever-growing transcript.

    Because the limit is applied to individual messages, not user/assistant turn
    pairs, older entries may drop off one by one. That is intentional: this state
    is optimized for lightweight routing context, not archival chat storage.
    """
    user_data = _get_user_data_dict(context)
    if user_data is None:
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
    """Store the latest nutrition analysis for correction-style follow-ups.

    This is separate from recent text history. Instead of storing prose, it keeps
    the structured payload the agent needs when the next user message says
    something like "it was actually 330 ml" or "change that to two eggs".

    Only three fields are persisted:
    - ``record_id`` for the agent's internal record update path;
    - ``meal_id`` for database persistence updates;
    - ``analysis`` for the previously extracted nutrition details that a
      correction prompt should modify.

    The function requires ``analysis`` to be a dict before storing anything. That
    guard prevents follow-up correction flows from receiving incomplete state that
    looks present but cannot actually be edited.
    """
    user_data = _get_user_data_dict(context)
    if user_data is None:
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


def remember_latest_expense_result(context: ContextTypes.DEFAULT_TYPE, result: Mapping[str, Any]) -> None:
    """Store the latest expense result so a follow-up text can correct it."""
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return

    analysis = result.get("analysis")
    if not isinstance(analysis, dict):
        return

    payload = {
        "record_id": str(result.get("record_id") or "").strip(),
        "expense_id": str(result.get("expense_id") or "").strip(),
        "analysis": analysis,
    }
    user_data[LAST_EXPENSE_RESULT_KEY] = payload


def remember_latest_tracking_result(context: ContextTypes.DEFAULT_TYPE, result: Mapping[str, Any]) -> None:
    """Store the last tracked entity in a workflow-agnostic shape.

    This is the broad "what was the last thing we created or updated?" memory.
    It exists mainly for delete and generic follow-up flows where the bot needs a
    single concrete target but does not yet know whether the user is referring to
    nutrition, an expense, or a recipe.

    The stored payload always includes ``task_type`` plus the possible identifier
    slots (``record_id``, ``meal_id``, ``expense_id``, ``dish_id``). Unused ids
    are stored as empty strings so downstream code can read one consistent shape
    without checking for missing keys. If ``analysis`` exists and is a dict, it is
    carried along as well, which gives the agent extra context for disambiguation
    or correction-like reasoning.

    If ``task_type`` is missing, nothing is stored. Without that field, later code
    would not know which delete/update path to take, so saving partial state would
    be more confusing than helpful.
    """
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return

    task_type = str(result.get("task_type") or "").strip()
    if not task_type:
        return

    analysis = result.get("analysis")
    payload = {
        "task_type": task_type,
        "record_id": str(result.get("record_id") or "").strip(),
        "meal_id": str(result.get("meal_id") or "").strip(),
        "expense_id": str(result.get("expense_id") or "").strip(),
        "dish_id": str(result.get("dish_id") or "").strip(),
    }
    if isinstance(analysis, dict):
        payload["analysis"] = analysis
    user_data[LAST_TRACKING_RESULT_KEY] = payload


def get_latest_nutrition_result(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    """Return the last nutrition result stored for follow-up corrections."""
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return None

    payload = user_data.get(LAST_NUTRITION_RESULT_KEY)
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("analysis"), dict):
        return None
    return payload


def get_latest_expense_result(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    """Return the last expense result stored for follow-up corrections."""
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return None

    payload = user_data.get(LAST_EXPENSE_RESULT_KEY)
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("analysis"), dict):
        return None
    return payload


def get_latest_tracking_result(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    """Return the last tracked entry stored for follow-up delete requests."""
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return None

    payload = user_data.get(LAST_TRACKING_RESULT_KEY)
    if not isinstance(payload, dict):
        return None
    task_type = str(payload.get("task_type") or "").strip()
    if not task_type:
        return None
    return payload


def clear_latest_nutrition_result(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove any pending nutrition correction context."""
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return
    user_data.pop(LAST_NUTRITION_RESULT_KEY, None)


def clear_latest_expense_result(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove stored expense-specific follow-up context.

    The bot keeps separate "latest nutrition" and "latest expense" memories so it
    can interpret corrections precisely. When a new result belongs to another
    domain, or when an expense entry has been deleted, this helper clears the
    expense-specific slot to avoid applying a later correction to stale data.

    This function only clears the expense correction payload. It does not touch
    recent chat history or the broader ``latest_tracking_result`` entry, because
    those pieces of state serve different follow-up behaviors.
    """
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return
    user_data.pop(LAST_EXPENSE_RESULT_KEY, None)


def clear_latest_tracking_result(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove any pending latest tracked-entry context."""
    user_data = _get_user_data_dict(context)
    if user_data is None:
        return
    user_data.pop(LAST_TRACKING_RESULT_KEY, None)
