"""Handlers for the separate vocabulary review bot."""

from __future__ import annotations

import logging
import re
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from ..agents import VocabularyAgent
from ..db import PostgresDatabase
from ..logging_context import bind_log_context, generate_process_id, get_log_context, reset_log_context
from ..vocabulary_review import is_shelf_request
from .dispatch import dispatch_next_due_vocabulary_review_for_user

logger = logging.getLogger(__name__)
_QUOTED_VOCAB_PATTERN = re.compile(
    r'(?:The correct word is|The French word is)\s+"([^"]+)"',
    re.IGNORECASE,
)


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
    """Activate the vocabulary bot for this Telegram user."""
    logger.info(
        "Received vocabulary bot start command",
        extra={
            "event": "vocabulary_bot_start_received",
            "telegram_user_id": update.effective_user.id if update.effective_user else None,
            "chat_id": _chat_id(update),
            "message_id": _message_id(update),
            "text_preview": _message_preview(update),
        },
    )
    if postgres_db is None or update.effective_user is None:
        await update.message.reply_text("Vocabulary training is not available right now.")
        return

    await _activate_user(postgres_db, update)
    await update.message.reply_text("Vocabulary training activated. I will send your review prompts here.")


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    agent: VocabularyAgent,
    postgres_db: Optional[PostgresDatabase] = None,
) -> None:
    """Handle a reply to a pending vocabulary review in the separate bot."""
    context_token = bind_log_context(
        process_id=get_log_context().get("process_id") or generate_process_id("vocab-bot"),
        telegram_user_id=update.effective_user.id if update.effective_user else None,
        update_id=update.update_id,
        action="vocabulary_bot_message",
        workflow="vocabulary_review",
    )
    try:
        logger.info(
            "Received vocabulary bot text message",
            extra={
                "event": "vocabulary_bot_message_received",
                "telegram_user_id": update.effective_user.id if update.effective_user else None,
                "chat_id": _chat_id(update),
                "message_id": _message_id(update),
                "text_preview": _message_preview(update),
            },
        )
        if postgres_db is None or update.effective_user is None:
            await update.message.reply_text("Vocabulary training is not available right now.")
            return

        user_id = await _activate_user(postgres_db, update)
        if await _try_shelf_quoted_review(update, postgres_db):
            return

        incoming_text = update.message.text or ""
        result = await agent.process_review_answer(update.effective_user.id, incoming_text, postgres_db)
        await update.message.reply_text(result["response"])

        if not result.get("dispatch_next_due_review"):
            return

        next_review_sent = await dispatch_next_due_vocabulary_review_for_user(
            context.application,
            postgres_db,
            user_id,
        )
        logger.info(
            "Handled vocabulary review answer in separate bot",
            extra={
                "event": "vocabulary_bot_review_answered",
                "telegram_user_id": update.effective_user.id,
                "next_due_review_sent": next_review_sent,
                "vocabulary_id": (
                    result["review_result"].vocabulary_id
                    if result.get("review_result") is not None
                    else None
                ),
            },
        )
    except Exception as exc:
        logger.exception("Error handling vocabulary bot message: %s", str(exc))
        try:
            await update.message.reply_text("Sorry, an error occurred while processing your vocabulary reply.")
        except Exception:
            pass
    finally:
        reset_log_context(context_token)


async def _activate_user(postgres_db: PostgresDatabase, update: Update) -> str:
    logger.info(
        "Persisting vocabulary bot activation",
        extra={
            "event": "vocabulary_bot_activation_persist_requested",
            "telegram_user_id": update.effective_user.id if update.effective_user else None,
            "username": update.effective_user.username if update.effective_user else None,
        },
    )
    user_id = await postgres_db.get_or_create_user(
        telegram_user_id=update.effective_user.id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        has_vocab_bot_activated=True,
    )
    bind_log_context(user_id=user_id)
    logger.info(
        "Activated vocabulary bot for Telegram user",
        extra={"event": "vocabulary_bot_activated", "resolved_user_id": user_id},
    )
    return user_id


async def _try_shelf_quoted_review(update: Update, postgres_db: PostgresDatabase) -> bool:
    message = update.message
    if message is None or update.effective_user is None:
        return False

    if not is_shelf_request(message.text or ""):
        return False

    quoted_prompt = _quoted_prompt_text(update)
    if quoted_prompt is None:
        return False

    reference = await postgres_db.get_recent_prompted_vocabulary_review_by_prompt(
        update.effective_user.id,
        quoted_prompt,
    )
    if reference is None:
        quoted_french_word = _quoted_french_word(quoted_prompt)
        if quoted_french_word is not None:
            reference = await postgres_db.get_recent_prompted_vocabulary_review_by_french_word(
                update.effective_user.id,
                quoted_french_word,
            )
    if reference is None:
        logger.info(
            "Could not resolve quoted vocabulary review prompt for shelving",
            extra={
                "event": "vocabulary_bot_quote_shelf_not_found",
                "telegram_user_id": update.effective_user.id,
                "quoted_prompt_preview": quoted_prompt[:120],
            },
        )
        await message.reply_text("I could not match that quoted review prompt to a vocabulary card.")
        return True

    await postgres_db.record_vocabulary_review_result(reference.vocabulary_id, shelved=True)
    logger.info(
        "Shelved vocabulary from quoted review prompt",
        extra={
            "event": "vocabulary_bot_quote_shelf_succeeded",
            "telegram_user_id": update.effective_user.id,
            "vocabulary_id": reference.vocabulary_id,
        },
    )
    await message.reply_text(f'Okay, I shelved "{reference.french_word}" for you.')
    return True


def _quoted_prompt_text(update: Update) -> str | None:
    message = update.message
    if message is None:
        return None

    reply_to_message = getattr(message, "reply_to_message", None)
    if reply_to_message is not None:
        reply_text = getattr(reply_to_message, "text", None)
        if reply_text:
            return reply_text.strip()

    quote = getattr(message, "quote", None)
    if quote is not None:
        quote_text = getattr(quote, "text", None)
        if quote_text:
            return quote_text.strip()

    return None


def _quoted_french_word(quoted_message: str) -> str | None:
    match = _QUOTED_VOCAB_PATTERN.search(quoted_message)
    if match is None:
        return None
    return match.group(1).strip()
