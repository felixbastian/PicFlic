"""Persistence helpers for Telegram bot flows."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..db import PostgresDatabase
from ..logging_context import bind_log_context
from ..models import ExpenseAnalysis, NutritionAnalysis, RecipeAnalysis

logger = logging.getLogger(__name__)


async def resolve_user_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: PostgresDatabase,
) -> str:
    """Resolve the warehouse user_id for the Telegram update."""
    if update.effective_user is None:
        raise ValueError("Cannot persist analysis without an effective Telegram user")

    pending_user_ids = context.application.bot_data.get("_picflic_user_ids", {})
    user_id = pending_user_ids.pop(update.update_id, None)
    if user_id is None:
        user_id = await postgres_db.get_or_create_user(
            telegram_user_id=update.effective_user.id,
            username=update.effective_user.username,
            first_name=update.effective_user.first_name,
            last_name=update.effective_user.last_name,
        )

    bind_log_context(user_id=user_id)
    logger.info("Resolved warehouse user id", extra={"event": "warehouse_user_resolved"})
    return user_id


async def persist_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: PostgresDatabase,
    result: dict,
) -> str:
    """Persist the routed analysis result and return a user-facing status line."""
    user_id = await resolve_user_id(update, context, postgres_db)
    task_type = result["task_type"]
    analysis = result["analysis"]
    bind_log_context(workflow=task_type)
    logger.info("Persisting workflow result", extra={"event": "workflow_result_persist", "task_type": task_type})

    if task_type == "expense":
        expense_id = await postgres_db.store_expense(user_id, ExpenseAnalysis.model_validate(analysis))
        result["expense_id"] = expense_id
        return "Expense added to the database."

    if task_type == "recipe":
        dish_id = await postgres_db.store_dish(user_id, RecipeAnalysis.model_validate(analysis))
        result["dish_id"] = dish_id
        return "Recipe added to your collection."

    meal_id = await postgres_db.store_consumption(
        user_id,
        NutritionAnalysis.model_validate(analysis),
        meal_id=result.get("record_id"),
    )
    result["meal_id"] = meal_id
    daily_calories = await postgres_db.get_daily_calories(user_id)
    return f"Today's total calories: {daily_calories}"
