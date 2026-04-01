"""Delete-latest-entry helpers for Telegram bot flows."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from ..agents import MainAgent
from ..db import PostgresDatabase
from .persistence import resolve_user_id
from .state import (
    clear_latest_nutrition_result,
    clear_latest_tracking_result,
    remember_text_turn,
)

logger = logging.getLogger(__name__)


async def apply_delete_latest_entry_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    """Delete exactly one latest tracked entry selected by the central text agent."""
    task_type = str(result.get("task_type") or "").strip()
    if task_type not in {"nutrition", "expense", "recipe"}:
        response = "I couldn't find a recent tracked entry to delete."
        await update.message.reply_text(response)
        remember_text_turn(context, incoming_text, [response], workflow_type="delete_latest_entry")
        return

    record_id = str(result.get("record_id") or "").strip()
    meal_id = str(result.get("meal_id") or "").strip()
    expense_id = str(result.get("expense_id") or "").strip()
    dish_id = str(result.get("dish_id") or "").strip()
    user_id: str | None = None

    if record_id:
        agent.delete_record(record_id)

    if postgres_db is not None:
        user_id = await resolve_user_id(update, context, postgres_db)
        if task_type == "nutrition" and meal_id:
            await postgres_db.delete_consumption(meal_id, user_id)
        elif task_type == "expense" and expense_id:
            await postgres_db.delete_expense(expense_id, user_id)
        elif task_type == "recipe" and dish_id:
            await postgres_db.delete_dish(dish_id, user_id)

    clear_latest_tracking_result(context)
    if task_type == "nutrition":
        clear_latest_nutrition_result(context)

    response = f"Deleted your last {task_type} entry."
    if postgres_db is not None and task_type == "nutrition":
        resolved_user_id = user_id or await resolve_user_id(update, context, postgres_db)
        daily_calories = await postgres_db.get_daily_calories(resolved_user_id)
        response = f"Deleted your last nutrition entry. Today's total calories: {daily_calories}"

    await update.message.reply_text(response)
    remember_text_turn(context, incoming_text, [response], workflow_type="delete_latest_entry")
    logger.info(
        "Deleted latest tracked entry from central text workflow",
        extra={
            "event": "delete_latest_entry_applied",
            "task_type": task_type,
            "record_id": record_id,
            "meal_id": meal_id,
            "expense_id": expense_id,
            "dish_id": dish_id,
        },
    )
