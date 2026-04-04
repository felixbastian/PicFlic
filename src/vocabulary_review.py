"""Helpers for scheduled vocabulary review prompts and answer evaluation."""

from __future__ import annotations

import random
import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher
import logging

from .models import (
    DueVocabularyReview,
    VocabularyReviewResult,
    VocabularyReviewStage,
    VocabularySentenceEvaluation,
    VocabularySynonymHint,
)
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
_SENTENCE_PROMPT_PROBABILITY = 0.25
_APOSTROPHE_VARIANTS = {
    "\u2019": "'",
    "\u2018": "'",
    "\u02bc": "'",
    "\u2032": "'",
    "\u00b4": "'",
    "`": "'",
}
logger = logging.getLogger(__name__)


def normalize_review_text(value: str) -> str:
    """Normalize French vocabulary answers for tolerant matching."""
    lowered = value.strip().lower()
    for variant, replacement in _APOSTROPHE_VARIANTS.items():
        lowered = lowered.replace(variant, replacement)
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


def build_review_prompt_text(english_description: str) -> str:
    """Build the outbound Telegram prompt body for a vocabulary review."""
    return (
        "Vocabulary review:\n"
        f"What is the French word for:\n{english_description}\n\n"
        "Reply with the French word. Reply 'p' or 'pass' to count it as wrong right away. "
        "Reply 'shelf' if you want me to stop reviewing this word."
    )


def build_sentence_prompt_text(french_word: str, *, second_chance: bool = False) -> str:
    """Build the prompt asking the user to use a vocabulary word in a sentence."""
    if second_chance:
        return (
            f'Try one more short French sentence using "{french_word}". '
            "Reply 'p' or 'pass' to skip this part."
        )
    return (
        f'Write one short French sentence using "{french_word}". '
        "Reply 'p' or 'pass' to skip this part."
    )


def build_review_prompt(review: DueVocabularyReview) -> str:
    """Build the outbound Telegram prompt for a due vocabulary review."""
    if review.awaiting_sentence:
        return build_sentence_prompt_text(
            review.french_word,
            second_chance=review.sentence_attempts > 0,
        )
    return build_review_prompt_text(review.english_description)


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


def build_sentence_prompt_response(
    review: DueVocabularyReview,
    result: VocabularyReviewResult,
) -> str:
    """Build the reply after a correct review answer that triggers sentence practice."""
    return (
        f"{build_review_response(review, result)}\n\n"
        f"{build_sentence_prompt_text(review.french_word)}"
    )


def build_sentence_retry_response(review: DueVocabularyReview, feedback: str) -> str:
    """Build the retry response after an incorrect sentence usage attempt."""
    cleaned_feedback = feedback.strip()
    return (
        f"{cleaned_feedback}\n\n"
        f'{build_sentence_prompt_text(review.french_word, second_chance=True)}'
    )


def build_sentence_success_response(
    review: DueVocabularyReview,
    evaluation: VocabularySentenceEvaluation,
) -> str:
    """Build the success response after an acceptable sentence."""
    corrected_sentence = (evaluation.corrected_sentence or "").strip()
    feedback = evaluation.feedback.strip()
    if corrected_sentence:
        if not feedback:
            return f'Corrected sentence: "{corrected_sentence}"'
        return (
            f"{feedback}\n"
            f'Corrected sentence: "{corrected_sentence}"'
        )
    return feedback or f'Nice. That sentence works with "{review.french_word}".'


def build_sentence_skip_response(review: DueVocabularyReview) -> str:
    """Build the response after the user skips sentence practice."""
    return f'No problem. We will skip the sentence for "{review.french_word}" and keep going.'


def build_sentence_failure_response(review: DueVocabularyReview, feedback: str) -> str:
    """Build the response after the second failed sentence attempt."""
    cleaned_feedback = feedback.strip()
    if cleaned_feedback:
        return f"{cleaned_feedback}\n\nWe will move on for now."
    return f'We will move on for now without a sentence for "{review.french_word}".'


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


def should_prompt_for_sentence_practice(
    review: DueVocabularyReview,
    *,
    draw: float | None = None,
) -> bool:
    """Return whether this correct answer should branch into sentence practice."""
    if review.used_in_sentence or review.awaiting_sentence:
        return False
    resolved_draw = random.random() if draw is None else draw
    return resolved_draw < _SENTENCE_PROMPT_PROBABILITY


def evaluate_vocabulary_sentence(
    review: DueVocabularyReview,
    sentence: str,
) -> VocabularySentenceEvaluation:
    """Evaluate whether the user used the target vocabulary word acceptably in a sentence."""
    prompt = (
        "You are helping a French vocabulary trainer. "
        "The user was asked to write one short French sentence using a specific target word. "
        "Return acceptable=true when the sentence uses the target word correctly and the sentence is understandable, "
        "even if there are small grammar, spelling, or agreement mistakes that do not seriously hurt understanding. "
        "Return acceptable=false only when the target word is missing, used with the wrong meaning or function, or "
        "the sentence is too broken to show correct usage. "
        "When acceptable=true, provide corrected_sentence as a polished French version of the user's sentence that "
        "keeps the same meaning, and feedback as one short encouraging English sentence. "
        "When acceptable=false, set corrected_sentence=null and feedback to a short English explanation of what is "
        "wrong with the usage and what to fix. "
        "Be lenient about minor mistakes, but strict about incorrect vocabulary usage. "
        "Return only the structured result."
    )
    user_text = (
        f"Target French word: {review.french_word}\n"
        f"English meaning: {review.english_description}\n"
        f"User sentence: {sentence.strip()}"
    )
    return _call_text_with_schema(
        prompt,
        user_text,
        VocabularySentenceEvaluation,
        "vocabulary_sentence_evaluation",
    )
