"""Prompt dispatch helpers for the separate vocabulary bot."""

from __future__ import annotations

import logging

from telegram.ext import Application

from ..db import PostgresDatabase
from ..logging_context import bind_log_context, generate_process_id, get_log_context, reset_log_context
from ..models import DueVocabularyReview
from ..vocabulary_review import build_review_prompt

logger = logging.getLogger(__name__)


async def dispatch_due_vocabulary_reviews(
    application: Application,
    postgres_db: PostgresDatabase,
    limit: int = 100,
) -> int:
    """Send newly due vocabulary review prompts without resending already-pending ones."""
    await postgres_db.expire_stale_vocabulary_conversations()
    sent_count = 0
    due_reviews = await postgres_db.list_due_vocabulary_reviews(limit=limit)
    for review in due_reviews:
        if await send_vocabulary_review_prompt(application, postgres_db, review):
            sent_count += 1
    return sent_count


async def send_vocabulary_review_prompt(
    application: Application,
    postgres_db: PostgresDatabase,
    review: DueVocabularyReview,
) -> bool:
    """Send or resend a single vocabulary review prompt."""
    context_token = bind_log_context(
        process_id=get_log_context().get("process_id") or generate_process_id("vocab-review"),
        user_id=review.user_id,
        telegram_user_id=review.telegram_user_id,
        action="vocabulary_review_dispatch",
        workflow="vocabulary_review",
    )
    try:
        prompt = build_review_prompt(review)
        await postgres_db.mark_vocabulary_review_prompted(review.vocabulary_id)
        await application.bot.send_message(chat_id=review.telegram_user_id, text=prompt)
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
