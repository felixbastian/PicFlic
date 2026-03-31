"""Telegram bot package."""

from ..utils import correct_nutrition_analysis
from .application import create_telegram_application
from .corrections import try_apply_latest_nutrition_correction
from .formatting import (
    format_multirow_query_response,
    format_query_response,
    format_recipe_response,
    format_result_response,
    format_vocabulary_response,
)
from .handlers import handle_message, start
from .persistence import persist_result, resolve_user_id
from .state import (
    clear_latest_nutrition_result,
    get_latest_nutrition_result,
    get_recent_history,
    remember_latest_nutrition_result,
    remember_text_turn,
)

__all__ = [
    "clear_latest_nutrition_result",
    "correct_nutrition_analysis",
    "create_telegram_application",
    "format_multirow_query_response",
    "format_query_response",
    "format_recipe_response",
    "format_result_response",
    "format_vocabulary_response",
    "get_latest_nutrition_result",
    "get_recent_history",
    "handle_message",
    "persist_result",
    "remember_latest_nutrition_result",
    "remember_text_turn",
    "resolve_user_id",
    "start",
    "try_apply_latest_nutrition_correction",
]
