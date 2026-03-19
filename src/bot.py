"""Telegram bot wiring and handlers."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .agent import PictoAgent
from .db import PostgresDatabase
from .models import ImageAnalysis

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text("Hi! Send me a photo of your food and I'll analyze it!")


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
                result = agent.process_image(image_path)
                analysis = result["analysis"]
                daily_calories: int | None = None
                if postgres_db is not None and update.effective_user is not None:
                    _, user_id = await persist_consumption(update, postgres_db, analysis)
                    daily_calories = await postgres_db.get_daily_calories(user_id)
                response = (
                    f"Category: {analysis['category']}\n"
                    f"Calories: {analysis['calories']}\n"
                    f"Tags: {', '.join(analysis.get('tags', []))}"
                )
                if daily_calories is not None:
                    response += f"\nToday's total calories: {daily_calories}"
                await update.message.reply_text(response)
                logger.info("Successfully analyzed photo from %s", user)
            except Exception as e:
                logger.error("Failed to analyze image from %s: %s", user, str(e))
                await update.message.reply_text(f"Error analyzing image: {e}")
            finally:
                os.unlink(image_path)
        else:
            logger.debug("Echoing text message from %s", user)
            await update.message.reply_text(update.message.text)
    except Exception as e:
        logger.exception("Error handling message: %s", str(e))
        try:
            await update.message.reply_text("Sorry, an error occurred while processing your message.")
        except Exception:
            pass


async def persist_consumption(
    update: Update,
    postgres_db: PostgresDatabase,
    analysis: dict,
) -> tuple[str, str]:
    """Persist a fact_consumption row for the Telegram user tied to this update."""
    if update.effective_user is None:
        raise ValueError("Cannot persist consumption without an effective Telegram user")

    user_id = getattr(update, "_picflic_user_id", None)
    if user_id is None:
        user_id = await postgres_db.get_or_create_user(
            telegram_user_id=update.effective_user.id,
            username=update.effective_user.username,
            first_name=update.effective_user.first_name,
            last_name=update.effective_user.last_name,
        )

    meal_id = await postgres_db.store_consumption(
        user_id=user_id,
        analysis=ImageAnalysis.model_validate(analysis),
    )
    return meal_id, user_id


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
