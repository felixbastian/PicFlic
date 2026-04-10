"""Handlers for the dedicated vocabulary conversation bot."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from ..db import PostgresDatabase
from ..logging_context import bind_log_context, generate_process_id, get_log_context, reset_log_context
from ..vocab_bot.conversation import VocabularyConversationTrainer

logger = logging.getLogger(__name__)


def _message_preview(update: Update) -> str | None:
    text = update.message.text if update.message is not None else None
    if not text:
        return None
    return text[:80]


def _chat_id(update: Update) -> int | None:
    chat = getattr(update, "effective_chat", None)
    if chat is None:
        return None
    return getattr(chat, "id", None)


def _message_id(update: Update) -> int | None:
    message = getattr(update, "message", None)
    if message is None:
        return None
    return getattr(message, "message_id", None)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: Optional[PostgresDatabase] = None,
) -> None:
    """Activate the dedicated conversation bot for this Telegram user."""
    logger.info(
        "Received vocabulary conversation bot start command",
        extra={
            "event": "vocabulary_conversation_bot_start_received",
            "telegram_user_id": update.effective_user.id if update.effective_user else None,
            "chat_id": _chat_id(update),
            "message_id": _message_id(update),
            "text_preview": _message_preview(update),
        },
    )
    if postgres_db is None or update.effective_user is None:
        await update.message.reply_text("Vocabulary conversation training is not available right now.")
        return

    await _activate_user(postgres_db, update)
    await update.message.reply_text(
        "Vocabulary conversation training activated. I will start a short daily chat with you here. "
        "Reply 'p' or 'pass' anytime to stop today's conversation."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    trainer: VocabularyConversationTrainer,
    postgres_db: Optional[PostgresDatabase] = None,
) -> None:
    """Handle inbound text for the dedicated conversation bot."""
    context_token = bind_log_context(
        process_id=get_log_context().get("process_id") or generate_process_id("vocab-conversation-bot"),
        telegram_user_id=update.effective_user.id if update.effective_user else None,
        update_id=update.update_id,
        action="vocabulary_conversation_bot_message",
        workflow="vocabulary_conversation",
    )
    try:
        logger.info(
            "Received vocabulary conversation bot text message",
            extra={
                "event": "vocabulary_conversation_bot_message_received",
                "telegram_user_id": update.effective_user.id if update.effective_user else None,
                "chat_id": _chat_id(update),
                "message_id": _message_id(update),
                "text_preview": _message_preview(update),
            },
        )
        if postgres_db is None or update.effective_user is None:
            await update.message.reply_text("Vocabulary conversation training is not available right now.")
            return

        await _activate_user(postgres_db, update)
        if await trainer.handle_active_conversation_message(update, postgres_db):
            return

        await update.message.reply_text(
            "No active conversation is waiting right now. I will start the next one here when it's due."
        )
    except Exception as exc:
        logger.exception("Error handling vocabulary conversation bot message: %s", str(exc))
        try:
            await update.message.reply_text(
                "Sorry, an error occurred while processing your conversation message."
            )
        except Exception:
            pass
    finally:
        reset_log_context(context_token)


async def _activate_user(postgres_db: PostgresDatabase, update: Update) -> str:
    logger.info(
        "Persisting vocabulary conversation bot activation",
        extra={
            "event": "vocabulary_conversation_bot_activation_persist_requested",
            "telegram_user_id": update.effective_user.id if update.effective_user else None,
            "username": update.effective_user.username if update.effective_user else None,
        },
    )
    user_id = await postgres_db.get_or_create_user(
        telegram_user_id=update.effective_user.id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        has_vocab_conversation_bot_activated=True,
    )
    bind_log_context(user_id=user_id)
    logger.info(
        "Activated vocabulary conversation bot for Telegram user",
        extra={"event": "vocabulary_conversation_bot_activated", "resolved_user_id": user_id},
    )
    return user_id
