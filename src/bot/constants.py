"""Shared constants for Telegram bot behavior."""

from __future__ import annotations

from ..models import RecipeAnalysis

QUERY_ALLOWED_TABLES = {
    "expense_query": ("fact_expenses",),
    "nutrition_query": ("fact_consumption",),
}
QUERY_TEMPLATE_FIELDS = {"result_value", "result_unit", "result_label", "period_label"}
RECIPE_ANALYSIS_FIELDS = set(RecipeAnalysis.model_fields)
RECENT_HISTORY_KEY = "_picflic_recent_messages"
RECENT_HISTORY_LIMIT = 3
LAST_NUTRITION_RESULT_KEY = "_picflic_last_nutrition_result"
WELCOME_MESSAGE = (
    "Hi! Send me a photo of your food or a receipt, ask about your tracked expenses and nutrition, "
    "send me a French word to practice vocabulary, or tell me to save a recipe to your collection."
)
