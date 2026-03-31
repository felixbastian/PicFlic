"""Telegram application wiring."""

from __future__ import annotations

from typing import Optional

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from ..agent import PictoAgent
from ..db import PostgresDatabase
from .handlers import handle_message, start


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
