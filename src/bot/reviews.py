"""Vocabulary review helpers for Telegram bot flows."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, ContextTypes

from ..db import PostgresDatabase
from ..logging_context import bind_log_context, generate_process_id, get_log_context, reset_log_context
from ..models import DueVocabularyReview
from ..vocabulary_review import (
    build_review_prompt,
    build_review_response,
    is_review_answer_correct,
    is_shelf_request,
)
from .state import remember_text_turn

logger = logging.getLogger(__name__)


async def handle_pending_vocabulary_review(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    postgres_db: PostgresDatabase,
    incoming_text: str,
) -> bool:
    """Handle the user's answer when a vocabulary review is currently pending."""
    if update.effective_user is None:
        return False

    pending_review = await postgres_db.get_pending_vocabulary_review(update.effective_user.id)
    if pending_review is None:
        return False

    bind_log_context(user_id=pending_review.user_id, workflow="vocabulary_review")
    review_result = await _record_review_result(postgres_db, pending_review, incoming_text)
    response = build_review_response(pending_review, review_result)
    await update.message.reply_text(response)
    next_review_sent = await dispatch_next_due_vocabulary_review_for_user(
        context.application,
        postgres_db,
        pending_review.user_id,
    )
    remember_text_turn(context, incoming_text, [response], workflow_type="vocabulary")
    logger.info(
        "Handled vocabulary review answer",
        extra={
            "event": "vocabulary_review_answered",
            "vocabulary_id": pending_review.vocabulary_id,
            "correct": review_result.correct,
            "shelved": review_result.shelved,
            "finished": review_result.finished,
            "next_due_review_sent": next_review_sent,
        },
    )
    return True


async def _record_review_result(
    postgres_db: PostgresDatabase,
    pending_review: DueVocabularyReview,
    incoming_text: str,
):
    if is_shelf_request(incoming_text):
        return await postgres_db.record_vocabulary_review_result(
            pending_review.vocabulary_id,
            shelved=True,
        )
    return await postgres_db.record_vocabulary_review_result(
        pending_review.vocabulary_id,
        correct=is_review_answer_correct(pending_review.french_word, incoming_text),
    )


async def dispatch_due_vocabulary_reviews(
    application: Application,
    postgres_db: PostgresDatabase,
    limit: int = 100,
) -> int:
    """Send due vocabulary review prompts, at most one pending prompt per user."""
    due_reviews = await postgres_db.list_due_vocabulary_reviews(limit=limit)
    sent_count = 0
    for review in due_reviews:
        if await send_vocabulary_review_prompt(application, postgres_db, review):
            sent_count += 1
    return sent_count


async def send_vocabulary_review_prompt(
    application: Application,
    postgres_db: PostgresDatabase,
    review: DueVocabularyReview,
) -> bool:
    """Send a single vocabulary review prompt and mark it as awaiting an answer."""
    context_token = bind_log_context(
        process_id=get_log_context().get("process_id") or generate_process_id("vocab-review"),
        user_id=review.user_id,
        telegram_user_id=review.telegram_user_id,
        action="vocabulary_review_dispatch",
        workflow="vocabulary_review",
    )
    try:
        prompt = build_review_prompt(review)
        await application.bot.send_message(chat_id=review.telegram_user_id, text=prompt)
        await postgres_db.mark_vocabulary_review_prompted(review.vocabulary_id)
        logger.info(
            "Sent vocabulary review prompt",
            extra={
                "event": "vocabulary_review_sent",
                "vocabulary_id": review.vocabulary_id,
                "current_review_stage": review.current_review_stage,
            },
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send vocabulary review prompt",
            extra={"event": "vocabulary_review_send_failed", "vocabulary_id": review.vocabulary_id},
        )
        return False
    finally:
        reset_log_context(context_token)


async def dispatch_next_due_vocabulary_review_for_user(
    application: Application,
    postgres_db: PostgresDatabase,
    user_id: str,
) -> bool:
    """Immediately send the next overdue vocabulary review for the same user, if one exists."""
    review = await postgres_db.get_next_due_vocabulary_review_for_user(user_id)
    if review is None:
        logger.info(
            "No follow-up vocabulary review due for user",
            extra={"event": "vocabulary_review_none_due_for_user", "user_id": user_id},
        )
        return False
    return await send_vocabulary_review_prompt(application, postgres_db, review)
