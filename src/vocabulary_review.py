"""Helpers for scheduled vocabulary review prompts and answer evaluation."""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher
import logging

from .models import DueVocabularyReview, VocabularyReviewResult, VocabularyReviewStage, VocabularySynonymHint
from .query_utils import _call_text_with_schema

_STAGE_INTERVALS: dict[VocabularyReviewStage, timedelta] = {
    "day": timedelta(days=1),
    "three_days": timedelta(days=3),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
_NEXT_STAGE: dict[VocabularyReviewStage, VocabularyReviewStage | None] = {
    "day": "three_days",
    "three_days": "week",
    "week": "month",
    "month": None,
}
_STAGE_LABELS: dict[VocabularyReviewStage, str] = {
    "day": "tomorrow",
    "three_days": "in 3 days",
    "week": "in 1 week",
    "month": "in 1 month",
}
_SHELF_KEYWORDS = {"shelf", "shelve", "archive", "skip", "pause", "stop"}
_PASS_KEYWORDS = {"p", "pass"}
logger = logging.getLogger(__name__)


def normalize_review_text(value: str) -> str:
    """Normalize French vocabulary answers for tolerant matching."""
    lowered = value.strip().lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = re.sub(r"[^a-z0-9'\-\s]", " ", without_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_shelf_request(answer: str) -> bool:
    """Return whether the user wants to shelf the current vocabulary card."""
    normalized = normalize_review_text(answer)
    if not normalized:
        return False
    return any(keyword in normalized.split() or keyword in normalized for keyword in _SHELF_KEYWORDS)


def is_pass_request(answer: str) -> bool:
    """Return whether the user wants to mark the card wrong without extra checking."""
    normalized = normalize_review_text(answer)
    return normalized in _PASS_KEYWORDS


def is_review_answer_correct(expected_word: str, answer: str) -> bool:
    """Accept correct answers with tolerant capitalization and small spelling mistakes."""
    normalized_expected = normalize_review_text(expected_word)
    normalized_answer = normalize_review_text(answer)
    if not normalized_expected or not normalized_answer:
        return False
    if normalized_expected == normalized_answer:
        return True
    if normalized_expected.replace(" ", "") == normalized_answer.replace(" ", ""):
        return True

    ratio = SequenceMatcher(None, normalized_expected, normalized_answer).ratio()
    threshold = 0.92 if len(normalized_expected) <= 5 else 0.84
    return ratio >= threshold


def get_stage_interval(stage: VocabularyReviewStage) -> timedelta:
    return _STAGE_INTERVALS[stage]


def get_next_stage(stage: VocabularyReviewStage) -> VocabularyReviewStage | None:
    return _NEXT_STAGE[stage]


def get_next_review_label(stage: VocabularyReviewStage | None) -> str | None:
    if stage is None:
        return None
    return _STAGE_LABELS[stage]


def build_review_prompt(review: DueVocabularyReview) -> str:
    """Build the outbound Telegram prompt for a due vocabulary review."""
    return (
        "Vocabulary review:\n"
        f"What is the French word for:\n{review.english_description}\n\n"
        "Reply with the French word. Reply 'p' or 'pass' to count it as wrong right away. "
        "Reply 'shelf' if you want me to stop reviewing this word."
    )


def build_review_response(
    review: DueVocabularyReview,
    result: VocabularyReviewResult,
) -> str:
    """Build the Telegram reply after the user answers a review prompt."""
    if result.shelved:
        return f'Okay, I shelved "{review.french_word}". I will stop asking you this word.'

    if result.correct:
        next_label = get_next_review_label(result.current_review_stage)
        if result.finished or next_label is None:
            return (
                f'Correct. The French word is "{review.french_word}". '
                "This vocabulary is now finished."
            )
        return (
            f'Correct. The French word is "{review.french_word}". '
            f"I will ask you again {next_label}."
        )

    retry_label = get_next_review_label(review.current_review_stage) or "later"
    return (
        f'Not quite. The correct word is "{review.french_word}". '
        f"I will ask you again {retry_label}."
    )


def build_synonym_second_chance_response(
    review: DueVocabularyReview,
    answer: str,
    distinction: str,
) -> str:
    """Build the retry response when the user gave a plausible synonym."""
    cleaned_distinction = distinction.strip().rstrip(".")
    return (
        f'Yes, "{answer.strip()}" also fits, but I am looking for "{review.french_word}". '
        f"{cleaned_distinction}. Please try again."
    )


def maybe_build_synonym_second_chance(review: DueVocabularyReview, answer: str) -> str | None:
    """Return a retry response when the answer is a plausible synonym of the target meaning."""
    normalized_answer = normalize_review_text(answer)
    normalized_expected = normalize_review_text(review.french_word)
    if not normalized_answer or normalized_answer == normalized_expected:
        return None

    prompt = (
        "You are helping a French vocabulary trainer. "
        "The user was asked for one specific French target word. "
        "Decide whether the user's different French answer is still a plausible synonym or near-synonym for the "
        "given English meaning. "
        "Return give_second_chance=true only when the user's answer is meaningfully related and should earn a retry "
        "instead of being marked wrong immediately. "
        "When give_second_chance=true, provide a short distinction in plain English that explains how the expected "
        "word differs in tone, register, usage, or specificity. "
        "When the answer is simply wrong, unrelated, or too far off, return give_second_chance=false and distinction=null. "
        "Do not treat spelling mistakes of the expected word as synonyms."
    )
    user_text = (
        f"English meaning: {review.english_description}\n"
        f"Expected French word: {review.french_word}\n"
        f"User answer: {answer.strip()}"
    )
    try:
        hint = _call_text_with_schema(prompt, user_text, VocabularySynonymHint, "vocabulary_synonym_hint")
    except Exception:
        logger.exception(
            "Failed to evaluate vocabulary synonym hint",
            extra={"event": "vocabulary_synonym_hint_failed", "vocabulary_id": review.vocabulary_id},
        )
        return None

    if not hint.give_second_chance or not hint.distinction:
        return None

    return build_synonym_second_chance_response(review, answer, hint.distinction)
