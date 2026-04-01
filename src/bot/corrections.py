"""Nutrition correction helpers for Telegram bot flows."""

from __future__ import annotations

import importlib
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..agents import MainAgent
from ..db import PostgresDatabase
from ..logging_context import bind_log_context
from .formatting import format_result_response
from .persistence import resolve_user_id
from .state import (
    get_latest_nutrition_result,
    get_recent_history,
    remember_latest_nutrition_result,
    remember_text_turn,
)

logger = logging.getLogger(__name__)


def _run_correct_nutrition_analysis(incoming_text: str, latest_analysis: dict, recent_history: list[dict[str, str]]):
    bot_module = importlib.import_module("src.bot")
    return bot_module.correct_nutrition_analysis(
        incoming_text,
        latest_analysis,
        metadata={"recent_history": recent_history},
    )


async def try_apply_latest_nutrition_correction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
) -> bool:
    """Apply a follow-up nutrition correction when the text clearly revises the last nutrition entry."""
    latest_result = get_latest_nutrition_result(context)
    if latest_result is None:
        return False

    correction = _run_correct_nutrition_analysis(
        incoming_text,
        latest_result["analysis"],
        get_recent_history(context),
    )
    if not correction.apply_correction or correction.analysis is None:
        return False

    record_id = str(latest_result.get("record_id") or "").strip()
    if record_id:
        agent.update_nutrition_record(record_id, correction.analysis)

    persistence_note = "Updated your previous nutrition entry."
    meal_id = str(latest_result.get("meal_id") or "").strip()
    if postgres_db is not None and meal_id:
        user_id = await resolve_user_id(update, context, postgres_db)
        bind_log_context(workflow="nutrition")
        await postgres_db.update_consumption(meal_id, user_id, correction.analysis)
        daily_calories = await postgres_db.get_daily_calories(user_id)
        persistence_note = f"Updated your previous nutrition entry. Today's total calories: {daily_calories}"

    corrected_result = {
        "task_type": "nutrition",
        "record_id": record_id,
        "meal_id": meal_id,
        "analysis": correction.analysis.to_dict(),
    }
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
        "Applied nutrition correction from follow-up text",
        extra={
            "event": "nutrition_correction_applied",
            "record_id": record_id,
            "meal_id": meal_id,
        },
    )
    return True
