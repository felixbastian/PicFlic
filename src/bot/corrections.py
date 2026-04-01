"""Nutrition correction helpers for Telegram bot flows."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..agents import MainAgent
from ..db import PostgresDatabase
from ..logging_context import bind_log_context
from ..models import NutritionAnalysis
from .formatting import format_result_response
from .persistence import resolve_user_id
from .state import remember_latest_nutrition_result, remember_text_turn
from .state import remember_latest_tracking_result

logger = logging.getLogger(__name__)


async def apply_nutrition_correction_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    """Persist and reply for a nutrition correction that was already selected by the main text agent."""
    analysis = NutritionAnalysis.model_validate(result["analysis"])
    record_id = str(result.get("record_id") or "").strip()
    meal_id = str(result.get("meal_id") or "").strip()

    if record_id:
        agent.update_nutrition_record(record_id, analysis)

    persistence_note = "Updated your previous nutrition entry."
    if postgres_db is not None and meal_id:
        user_id = await resolve_user_id(update, context, postgres_db)
        bind_log_context(workflow="nutrition")
        await postgres_db.update_consumption(meal_id, user_id, analysis)
        daily_calories = await postgres_db.get_daily_calories(user_id)
        persistence_note = f"Updated your previous nutrition entry. Today's total calories: {daily_calories}"

    corrected_result = {
        "task_type": "nutrition",
        "record_id": record_id,
        "meal_id": meal_id,
        "analysis": analysis.to_dict(),
    }
    remember_latest_tracking_result(context, corrected_result)
    remember_latest_nutrition_result(context, corrected_result)
    response = format_result_response(corrected_result, persistence_note)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    remember_text_turn(
        context,
        incoming_text,
        [response],
        workflow_type="nutrition_correction",
    )
    logger.info(
        "Applied nutrition correction from central text workflow",
        extra={
            "event": "nutrition_correction_applied",
            "record_id": record_id,
            "meal_id": meal_id,
        },
    )
