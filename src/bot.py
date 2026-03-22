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
from .logging_context import bind_log_context, generate_process_id, get_log_context, reset_log_context
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
    context_token = bind_log_context(
        process_id=get_log_context().get("process_id") or generate_process_id("telegram"),
        telegram_user_id=update.effective_user.id if update.effective_user else None,
        update_id=update.update_id,
        action="telegram_message",
    )
    try:
        user = update.effective_user.username if update.effective_user else "unknown"
        logger.info(
            "Handling Telegram message",
            extra={
                "event": "telegram_message_received",
                "message_kind": "photo" if update.message.photo else "text",
                "has_caption": bool(update.message.caption),
                "text_preview": (update.message.text or "")[:200],
            },
        )

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
                logger.info(
                    "Image workflow produced result",
                    extra={
                        "event": "agent_image_result",
                        "task_type": result["task_type"],
                        "analysis": result["analysis"],
                    },
                )
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
            logger.info(
                "Text workflow produced result",
                extra={
                    "event": "agent_text_result",
                    "workflow_type": result["workflow_type"],
                    "explanation": result.get("explanation"),
                    "sql_query": result.get("sql_query"),
                },
            )
            if result["workflow_type"] == "echo":
                await update.message.reply_text(update.message.text or "")
            else:
                if postgres_db is None:
                    await update.message.reply_text("Database-backed questions are not available right now.")
                    return

                await update.message.reply_text(result["explanation"])
                user_id = await resolve_user_id(update, context, postgres_db)
                rows = await postgres_db.execute_guarded_query(
                    result["sql_query"],
                    user_id,
                    _QUERY_ALLOWED_TABLES[result["workflow_type"]],
                )
                logger.info(
                    "Query workflow returned rows",
                    extra={"event": "agent_query_rows", "rows": rows, "workflow_type": result["workflow_type"]},
                )
                if len(rows) <= 1:
                    await update.message.reply_text(format_query_response(result, rows[0] if rows else {}))
                else:
                    await update.message.reply_text(format_multirow_query_response(result, rows))
    except Exception as e:
        logger.exception("Error handling message: %s", str(e))
        try:
            await update.message.reply_text("Sorry, an error occurred while processing your message.")
        except Exception:
            pass
    finally:
        reset_log_context(context_token)


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


def format_multirow_query_response(
    result: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    max_lines: int = 10,
) -> str:
    """Render a compact multi-row query response for grouped breakdowns."""
    if not rows:
        return "No results found for your query."

    period_label = str(rows[0].get("period_label") or "the requested period").strip()
    visible_rows = rows[:max_lines]
    lines = [f"Breakdown for {period_label}:"]
    for row in visible_rows:
        result_label = str(row.get("result_label") or "unknown").strip()
        result_value = _format_query_value(row.get("result_value"))
        result_unit = str(row.get("result_unit") or "").strip()
        if result_unit:
            lines.append(f"{result_label}: {result_value} {result_unit}")
        else:
            lines.append(f"{result_label}: {result_value}")

    if len(rows) > max_lines:
        lines.append(f"... and {len(rows) - max_lines} more rows.")

    return "\n".join(lines)


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
