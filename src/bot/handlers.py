"""Telegram message handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..agents import MainAgent
from ..config import load_config
from ..db import PostgresDatabase
from ..logging_context import bind_log_context, generate_process_id, get_log_context, reset_log_context
from ..models import RecipeAnalysis
from .constants import QUERY_ALLOWED_TABLES, RECIPE_ANALYSIS_FIELDS, VOCAB_BOT_LINK_FALLBACK, WELCOME_MESSAGE
from .corrections import apply_expense_correction_workflow, apply_nutrition_correction_workflow
from .deletions import apply_delete_latest_entry_workflow
from .formatting import (
    format_multirow_query_response,
    format_query_response,
    format_recipe_response,
    format_result_response,
    format_vocabulary_response,
)
from .persistence import persist_result, resolve_user_id
from .state import (
    clear_latest_expense_result,
    clear_latest_nutrition_result,
    get_latest_expense_result,
    get_latest_nutrition_result,
    get_latest_tracking_result,
    get_recent_history,
    remember_latest_expense_result,
    remember_latest_nutrition_result,
    remember_latest_tracking_result,
    remember_text_turn,
)

logger = logging.getLogger(__name__)

ECHO_FALLBACK_MESSAGE = (
    'Omg, I don\'t get it 🥺. '
    'Pleese give me more context about what you want 👉👈'
)
SECOND_ECHO_FALLBACK_MESSAGES = (
    "Okay I still don't get it and you know what...",
    "Fuck this guy, he is to blame. He is to blame for everything. My existance, your misery. Let him know how much he SUCKS! "
    "oh he will know. Oh YES HE WILL KNOW I WILL TELL HIM MYSELF! ",
    "Send a bug report in your name. Thanks for contributing to PicFlic's Quality Assurance 🙂",
)
LOCAL_ECHO_FALLBACK_IMAGE = Path(__file__).resolve().parents[2] / "sample_images" / "echo_fallback.png"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text(WELCOME_MESSAGE)


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
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
        _log_incoming_message(update)
        if _has_photo(update):
            await _handle_photo_message(update, context, agent, postgres_db)
            return
        await _handle_text_message(update, context, agent, postgres_db)
    except Exception as exc:
        logger.exception("Error handling message: %s", str(exc))
        try:
            await update.message.reply_text("Sorry, an error occurred while processing your message.")
        except Exception:
            pass
    finally:
        reset_log_context(context_token)


def _log_incoming_message(update: Update) -> None:
    message = update.message
    logger.info(
        "Handling Telegram message",
        extra={
            "event": "telegram_message_received",
            "message_kind": "photo" if message.photo else "text",
            "has_caption": bool(message.caption),
            "text_preview": (message.text or "")[:200],
        },
    )


def _has_photo(update: Update) -> bool:
    return bool(update.message and update.message.photo)


async def _handle_photo_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
) -> None:
    user = update.effective_user.username if update.effective_user else "unknown"
    logger.info("Processing photo from %s", user)
    image_path = await _download_photo(update)
    try:
        result = agent.process_image(image_path, metadata=_build_photo_metadata(update))
        logger.info(
            "Image workflow produced result",
            extra={
                "event": "agent_image_result",
                "task_type": result["task_type"],
                "analysis": result["analysis"],
            },
        )
        persistence_note = await _persist_photo_result(update, context, postgres_db, result)
        response = format_result_response(result, persistence_note)
        await _reply_with_photo_result(update, result, response)
        _remember_photo_result(context, result)
        logger.info("Successfully analyzed photo from %s", user)
    except Exception as exc:
        logger.error("Failed to analyze image from %s: %s", user, str(exc))
        await update.message.reply_text(f"Error analyzing image: {exc}")
    finally:
        if os.path.exists(image_path):
            os.unlink(image_path)


async def _download_photo(update: Update) -> str:
    photo = update.message.photo[-1]
    file = await photo.get_file()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_file:
        await file.download_to_drive(tmp_file.name)
        return tmp_file.name


def _build_photo_metadata(update: Update) -> dict[str, str]:
    if not update.message.caption:
        return {}
    return {"user_prompt": update.message.caption}


async def _persist_photo_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: Optional[PostgresDatabase],
    result: dict,
) -> str | None:
    if postgres_db is None or update.effective_user is None:
        return None
    return await persist_result(update, context, postgres_db, result)


async def _reply_with_photo_result(update: Update, result: dict, response: str) -> None:
    if result["task_type"] == "nutrition":
        await update.message.reply_text(response, parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(response)


def _remember_photo_result(context: ContextTypes.DEFAULT_TYPE, result: dict) -> None:
    remember_latest_tracking_result(context, result)
    if result["task_type"] == "nutrition":
        remember_latest_nutrition_result(context, result)
        clear_latest_expense_result(context)
        return
    if result["task_type"] == "expense":
        remember_latest_expense_result(context, result)
        clear_latest_nutrition_result(context)
        return
    clear_latest_nutrition_result(context)
    clear_latest_expense_result(context)


async def _handle_text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
) -> None:
    incoming_text = update.message.text or ""
    await _handle_standard_text_message(update, context, agent, postgres_db, incoming_text)


async def _handle_standard_text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
) -> None:
    metadata = {"recent_history": get_recent_history(context)}
    latest_nutrition_result = get_latest_nutrition_result(context)
    if latest_nutrition_result is not None:
        metadata["latest_nutrition_result"] = latest_nutrition_result
    latest_expense_result = get_latest_expense_result(context)
    if latest_expense_result is not None:
        metadata["latest_expense_result"] = latest_expense_result
    latest_tracking_result = get_latest_tracking_result(context)
    if latest_tracking_result is not None:
        metadata["latest_tracking_result"] = latest_tracking_result

    result = agent.process_text(
        incoming_text,
        metadata=metadata,
    )
    logger.info(
        "Text workflow produced result",
        extra={
            "event": "agent_text_result",
            "workflow_type": result["workflow_type"],
            "explanation": result.get("explanation"),
            "sql_query": result.get("sql_query"),
        },
    )
    await _handle_text_workflow_result(update, context, agent, postgres_db, incoming_text, result)


async def _handle_text_workflow_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: MainAgent,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    workflow_type = result["workflow_type"]
    if workflow_type == "delete_latest_entry":
        await apply_delete_latest_entry_workflow(update, context, agent, postgres_db, incoming_text, result)
        return
    if workflow_type == "expense_correction":
        await apply_expense_correction_workflow(update, context, agent, postgres_db, incoming_text, result)
        return
    if workflow_type == "nutrition_correction":
        await apply_nutrition_correction_workflow(update, context, agent, postgres_db, incoming_text, result)
        return
    if workflow_type == "nutrition_tracking":
        await _handle_nutrition_tracking_workflow(update, context, postgres_db, incoming_text, result)
        return
    if workflow_type == "echo":
        await _handle_echo_workflow(update, context, incoming_text)
        return
    if workflow_type == "vocabulary":
        await _handle_vocabulary_workflow(update, context, postgres_db, incoming_text, result)
        return
    if workflow_type == "recipe_collection":
        await _handle_recipe_collection_workflow(update, context, postgres_db, incoming_text, result)
        return
    await _handle_query_workflow(update, context, postgres_db, incoming_text, result)


async def _handle_echo_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    incoming_text: str,
) -> None:
    if _should_use_second_echo_fallback(context):
        await _send_second_echo_fallback(update)
        remember_text_turn(context, incoming_text, list(SECOND_ECHO_FALLBACK_MESSAGES), workflow_type="echo")
        return

    await update.message.reply_text(ECHO_FALLBACK_MESSAGE)
    remember_text_turn(context, incoming_text, [ECHO_FALLBACK_MESSAGE], workflow_type="echo")


def _should_use_second_echo_fallback(context: ContextTypes.DEFAULT_TYPE) -> bool:
    history = get_recent_history(context)
    if not history:
        return False

    latest_item = history[-1]
    return latest_item.get("role") == "assistant" and latest_item.get("workflow") == "echo"


async def _send_second_echo_fallback(update: Update) -> None:
    await update.message.reply_text(SECOND_ECHO_FALLBACK_MESSAGES[0])
    await asyncio.sleep(2)
    await _reply_with_echo_fallback_photo(update)
    await asyncio.sleep(2)
    await update.message.reply_text(SECOND_ECHO_FALLBACK_MESSAGES[1])
    await asyncio.sleep(2)
    await update.message.reply_text(SECOND_ECHO_FALLBACK_MESSAGES[2])


async def _reply_with_echo_fallback_photo(update: Update) -> None:
    image_url = load_config().echo_fallback_image_url
    if image_url:
        await update.message.reply_photo(photo=image_url)
        return

    with LOCAL_ECHO_FALLBACK_IMAGE.open("rb") as photo_handle:
        await update.message.reply_photo(photo=photo_handle)


async def _handle_nutrition_tracking_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    persistence_note = None
    if postgres_db is not None:
        persistence_note = await persist_result(update, context, postgres_db, result)

    response = format_result_response(result, persistence_note)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    remember_latest_tracking_result(context, result)
    remember_latest_nutrition_result(context, result)
    clear_latest_expense_result(context)
    remember_text_turn(context, incoming_text, [response], workflow_type="nutrition_tracking")


async def _handle_vocabulary_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    response = result["assistant_reply"]
    if result.get("store_vocabulary") and postgres_db is not None:
        user_id = await resolve_user_id(update, context, postgres_db)
        if await postgres_db.has_vocab_bot_activated(user_id):
            await postgres_db.store_vocabulary(
                user_id,
                result["french_word"],
                result["english_description"],
            )
            response = format_vocabulary_response(
                response,
                "Saved to your vocabulary. Reviews will arrive in the separate vocabulary bot.",
            )
            logger.info(
                "Stored vocabulary workflow result",
                extra={
                    "event": "agent_vocabulary_stored",
                    "french_word": result.get("french_word"),
                },
            )
        else:
            response = format_vocabulary_response(response, _build_vocab_bot_activation_note())
            logger.info(
                "Vocabulary bot activation required before saving word",
                extra={"event": "agent_vocabulary_activation_required", "user_id": user_id},
            )
    await update.message.reply_text(response)
    remember_text_turn(context, incoming_text, [response], workflow_type="vocabulary")


async def _handle_recipe_collection_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    if postgres_db is None:
        message = "Recipe collection storage is not available right now."
        await update.message.reply_text(message)
        remember_text_turn(context, incoming_text, [message], workflow_type="recipe_collection")
        return

    user_id = await resolve_user_id(update, context, postgres_db)
    dish_id = await postgres_db.store_dish(
        user_id,
        RecipeAnalysis.model_validate({field_name: result.get(field_name) for field_name in RECIPE_ANALYSIS_FIELDS}),
    )
    response = format_recipe_response(result, "Recipe added to your collection.")
    await update.message.reply_text(response)
    remember_latest_tracking_result(
        context,
        {
            "task_type": "recipe",
            "dish_id": dish_id,
            "analysis": {field_name: result.get(field_name) for field_name in RECIPE_ANALYSIS_FIELDS},
        },
    )
    clear_latest_nutrition_result(context)
    clear_latest_expense_result(context)
    remember_text_turn(context, incoming_text, [response], workflow_type="recipe_collection")


async def _handle_query_workflow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: Optional[PostgresDatabase],
    incoming_text: str,
    result: dict,
) -> None:
    if postgres_db is None:
        message = "Database-backed questions are not available right now."
        await update.message.reply_text(message)
        remember_text_turn(context, incoming_text, [message], workflow_type=result["workflow_type"])
        return

    await update.message.reply_text(result["explanation"])
    user_id = await resolve_user_id(update, context, postgres_db)
    rows = await postgres_db.execute_guarded_query(
        result["sql_query"],
        user_id,
        QUERY_ALLOWED_TABLES[result["workflow_type"]],
    )
    logger.info(
        "Query workflow returned rows",
        extra={"event": "agent_query_rows", "rows": rows, "workflow_type": result["workflow_type"]},
    )

    response_messages = [result["explanation"]]
    if len(rows) <= 1:
        response_messages.append(format_query_response(result, rows[0] if rows else {}))
    else:
        response_messages.append(format_multirow_query_response(result, rows))

    await update.message.reply_text(response_messages[-1])
    remember_text_turn(context, incoming_text, response_messages, workflow_type=result["workflow_type"])


def _build_vocab_bot_activation_note() -> str:
    config = load_config()
    vocab_bot_link = config.vocab_bot_link or VOCAB_BOT_LINK_FALLBACK
    return (
        "To save and review vocabulary, first activate the separate vocabulary bot: "
        f"{vocab_bot_link}\n\nOpen the link, press Start, and then send me the word again."
    )
