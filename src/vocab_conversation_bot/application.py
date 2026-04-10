"""Dedicated vocabulary conversation bot application wiring."""

from __future__ import annotations

from typing import Optional

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from ..db import PostgresDatabase
from ..vocab_bot.conversation import VocabularyConversationTrainer
from .handlers import handle_message, start


def create_vocabulary_conversation_telegram_application(
    trainer: VocabularyConversationTrainer,
    token: str,
    postgres_db: Optional[PostgresDatabase] = None,
) -> Application:
    """Create and configure the dedicated vocabulary conversation bot application."""
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", lambda update, context: start(update, context, postgres_db)))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            lambda update, context: handle_message(update, context, trainer, postgres_db),
        )
    )
    return application
