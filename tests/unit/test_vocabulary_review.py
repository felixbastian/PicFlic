from src.models import DueVocabularyReview
from src.vocabulary_review import (
    build_review_prompt,
    is_review_answer_correct,
    normalize_review_text,
    should_prompt_for_sentence_practice,
)


def test_normalize_review_text_unifies_curly_apostrophes():
    assert normalize_review_text("l’abri") == "l'abri"


def test_is_review_answer_correct_accepts_curly_apostrophe_variant():
    assert is_review_answer_correct("l'abri", "l’abri") is True


def test_build_review_prompt_uses_sentence_prompt_when_sentence_is_pending():
    review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello",
        awaiting_sentence=True,
        sentence_attempts=1,
    )

    assert build_review_prompt(review) == (
        'Try one more short French sentence using "bonjour". '
        "Reply 'p' or 'pass' to skip this part."
    )


def test_should_prompt_for_sentence_practice_skips_words_already_used_in_sentence():
    review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello",
        used_in_sentence=True,
    )

    assert should_prompt_for_sentence_practice(review, draw=0.0) is False
