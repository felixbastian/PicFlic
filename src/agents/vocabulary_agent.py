"""Dedicated vocabulary review agent."""

from __future__ import annotations

import logging

from ..db import PostgresDatabase
from ..models import DueVocabularyReview
from ..vocabulary_review import (
    build_review_response,
    build_sentence_failure_response,
    build_sentence_prompt_response,
    build_sentence_retry_response,
    build_sentence_skip_response,
    build_sentence_success_response,
    evaluate_vocabulary_sentence,
    is_pass_request,
    is_review_answer_correct,
    is_shelf_request,
    maybe_build_synonym_second_chance,
    should_prompt_for_sentence_practice,
)

logger = logging.getLogger(__name__)


class VocabularyAgent:
    """Agent responsible for vocabulary review answer processing."""

    async def process_review_answer(
        self,
        telegram_user_id: int,
        answer_text: str,
        db: PostgresDatabase,
    ) -> dict:
        trimmed_answer = answer_text.strip()
        pending_review = await db.get_pending_vocabulary_review(telegram_user_id)
        if pending_review is None:
            logger.info(
                "No pending vocabulary review for Telegram user",
                extra={"event": "vocabulary_review_none_pending", "telegram_user_id": telegram_user_id},
            )
            return {
                "response": "No vocabulary review is waiting right now. Use the main bot to save new words first.",
                "review_result": None,
                "dispatch_next_due_review": False,
            }

        if pending_review.awaiting_sentence:
            return await _process_sentence_answer(pending_review, trimmed_answer, db)
        return await _process_word_answer(pending_review, trimmed_answer, db)


async def _process_word_answer(
    pending_review: DueVocabularyReview,
    answer_text: str,
    db: PostgresDatabase,
) -> dict:
    if is_shelf_request(answer_text):
        return await _build_persisted_review_response(
            pending_review,
            db,
            correct=False,
            shelved=True,
            request_sentence_practice=False,
            dispatch_next_due_review=True,
        )

    if is_pass_request(answer_text):
        return await _build_persisted_review_response(
            pending_review,
            db,
            correct=False,
            shelved=False,
            request_sentence_practice=False,
            dispatch_next_due_review=True,
        )

    if is_review_answer_correct(pending_review.french_word, answer_text):
        request_sentence_practice = should_prompt_for_sentence_practice(pending_review)
        return await _build_persisted_review_response(
            pending_review,
            db,
            correct=True,
            shelved=False,
            request_sentence_practice=request_sentence_practice,
            dispatch_next_due_review=not request_sentence_practice,
        )

    second_chance_response = maybe_build_synonym_second_chance(pending_review, answer_text)
    if second_chance_response is not None:
        return {
            "response": second_chance_response,
            "review_result": None,
            "dispatch_next_due_review": False,
        }

    return await _build_persisted_review_response(
        pending_review,
        db,
        correct=False,
        shelved=False,
        request_sentence_practice=False,
        dispatch_next_due_review=True,
    )


async def _build_persisted_review_response(
    pending_review: DueVocabularyReview,
    db: PostgresDatabase,
    *,
    correct: bool,
    shelved: bool,
    request_sentence_practice: bool,
    dispatch_next_due_review: bool,
) -> dict:
    review_result = await db.record_vocabulary_review_result(
        pending_review.vocabulary_id,
        correct=correct,
        shelved=shelved,
        request_sentence_practice=request_sentence_practice,
    )
    if review_result.awaiting_sentence:
        response = build_sentence_prompt_response(pending_review, review_result)
        dispatch_next_due_review = False
    else:
        response = build_review_response(pending_review, review_result)

    logger.info(
        "Built vocabulary review response",
        extra={
            "event": "vocabulary_review_response_built",
            "vocabulary_id": pending_review.vocabulary_id,
            "correct": review_result.correct,
            "shelved": review_result.shelved,
            "awaiting_sentence": review_result.awaiting_sentence,
        },
    )
    return {
        "response": response,
        "review_result": review_result,
        "dispatch_next_due_review": dispatch_next_due_review,
    }


async def _process_sentence_answer(
    pending_review: DueVocabularyReview,
    answer_text: str,
    db: PostgresDatabase,
) -> dict:
    if is_shelf_request(answer_text):
        return await _build_persisted_review_response(
            pending_review,
            db,
            correct=False,
            shelved=True,
            request_sentence_practice=False,
            dispatch_next_due_review=True,
        )

    if is_pass_request(answer_text):
        await db.clear_vocabulary_sentence_prompt(pending_review.vocabulary_id)
        return {
            "response": build_sentence_skip_response(pending_review),
            "review_result": None,
            "dispatch_next_due_review": True,
        }

    sentence_evaluation = evaluate_vocabulary_sentence(pending_review, answer_text)
    if sentence_evaluation.acceptable:
        await db.mark_vocabulary_used_in_sentence(pending_review.vocabulary_id)
        return {
            "response": build_sentence_success_response(pending_review, sentence_evaluation),
            "review_result": None,
            "dispatch_next_due_review": True,
        }

    if pending_review.sentence_attempts < 1:
        await db.increment_vocabulary_sentence_attempts(pending_review.vocabulary_id)
        return {
            "response": build_sentence_retry_response(pending_review, sentence_evaluation.feedback),
            "review_result": None,
            "dispatch_next_due_review": False,
        }

    await db.clear_vocabulary_sentence_prompt(pending_review.vocabulary_id)
    return {
        "response": build_sentence_failure_response(pending_review, sentence_evaluation.feedback),
        "review_result": None,
        "dispatch_next_due_review": True,
    }


__all__ = ["VocabularyAgent"]
