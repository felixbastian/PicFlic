"""Telegram bot wiring and handlers."""

from __future__ import annotations

import logging
import os
import tempfile
from decimal import Decimal
from string import Formatter
from typing import Any, Mapping, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .agent import PictoAgent
from .db import PostgresDatabase
from .models import ExpenseAnalysis, NutritionAnalysis

logger = logging.getLogger(__name__)
_QUERY_ALLOWED_TABLES = {
    "expense_query": ("fact_expenses",),
    "nutrition_query": ("fact_consumption",),
}
_QUERY_TEMPLATE_FIELDS = {"result_value", "result_unit", "result_label", "period_label"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "Hi! Send me a photo of your food or a receipt, or ask about your tracked expenses and nutrition."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: PictoAgent,
    postgres_db: Optional[PostgresDatabase] = None,
) -> None:
    """Handle incoming Telegram messages."""
    try:
        user = update.effective_user.username if update.effective_user else "unknown"

        if update.message.photo:
            logger.info("Processing photo from %s", user)
            photo = update.message.photo[-1]
            file = await photo.get_file()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
                await file.download_to_drive(tmp_file.name)
                image_path = tmp_file.name

            try:
                metadata: dict[str, str] = {}
                if update.message.caption:
                    metadata["user_prompt"] = update.message.caption

                result = agent.process_image(image_path, metadata=metadata)
                persistence_note: str | None = None
                if postgres_db is not None and update.effective_user is not None:
                    persistence_note = await persist_result(update, context, postgres_db, result)
                response = format_result_response(result, persistence_note)
                await update.message.reply_text(response)
                logger.info("Successfully analyzed photo from %s", user)
            except Exception as e:
                logger.error("Failed to analyze image from %s: %s", user, str(e))
                await update.message.reply_text(f"Error analyzing image: {e}")
            finally:
                os.unlink(image_path)
        else:
            logger.debug("Processing text message from %s", user)
            result = agent.process_text(update.message.text or "")
            if result["workflow_type"] == "echo":
                await update.message.reply_text(update.message.text or "")
            else:
                if postgres_db is None:
                    await update.message.reply_text("Database-backed questions are not available right now.")
                    return

                await update.message.reply_text(result["explanation"])
                user_id = await resolve_user_id(update, context, postgres_db)
                row = await postgres_db.execute_guarded_query(
                    result["sql_query"],
                    user_id,
                    _QUERY_ALLOWED_TABLES[result["workflow_type"]],
                )
                await update.message.reply_text(format_query_response(result, row))
    except Exception as e:
        logger.exception("Error handling message: %s", str(e))
        try:
            await update.message.reply_text("Sorry, an error occurred while processing your message.")
        except Exception:
            pass


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

    if task_type == "expense":
        await postgres_db.store_expense(user_id, ExpenseAnalysis.model_validate(analysis))
        return "Expense added to the database."

    await postgres_db.store_consumption(user_id, NutritionAnalysis.model_validate(analysis))
    daily_calories = await postgres_db.get_daily_calories(user_id)
    return f"Today's total calories: {daily_calories}"


def format_result_response(result: dict, persistence_note: str | None = None) -> str:
    """Format the specialist result for Telegram."""
    task_type = result["task_type"]
    analysis = result["analysis"]
    if task_type == "expense":
        lines: list[str] = []
        if persistence_note:
            lines.append(persistence_note)
        lines.extend(
            [
                f"Total: EUR {float(analysis['expense_total_amount_in_euros']):.2f}",
                f"Category: {analysis['category']}",
                f"Description: {analysis['description']}",
            ]
        )
        return "\n".join(lines)

    lines = [
        f"Category: {analysis['category']}",
        f"Calories: {analysis['calories']}",
        f"Tags: {', '.join(analysis.get('tags', []))}",
    ]
    if persistence_note:
        lines.append(persistence_note)
    return "\n".join(lines)


def format_query_response(result: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    """Render a planned SQL query result into a short Telegram message."""
    template = result.get("response_template") or (
        "The total is {result_value} {result_unit} for {result_label} in {period_label}."
    )
    referenced_fields = {
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name is not None and field_name != ""
    }
    if not referenced_fields.issubset(_QUERY_TEMPLATE_FIELDS):
        template = "The total is {result_value} {result_unit} for {result_label} in {period_label}."

    payload = {
        "result_value": _format_query_value(row.get("result_value")),
        "result_unit": str(row.get("result_unit") or "").strip(),
        "result_label": str(row.get("result_label") or "the requested data").strip(),
        "period_label": str(row.get("period_label") or "the requested period").strip(),
    }
    rendered = template.format(**payload)
    return " ".join(rendered.split())


def _format_query_value(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else f"{value:.2f}"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    return str(value)


def create_telegram_application(
    agent: PictoAgent,
    token: str,
    postgres_db: Optional[PostgresDatabase] = None,
) -> Application:
    """Create and configure the Telegram bot application."""
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(
            filters.TEXT | filters.PHOTO,
            lambda update, context: handle_message(update, context, agent, postgres_db),
        )
    )

    return application
