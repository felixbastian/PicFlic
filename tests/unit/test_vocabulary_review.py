from src.models import DueVocabularyReview, VocabularyStoredExamples
from src.vocabulary_review import (
    append_vocabulary_examples_to_description,
    build_sentence_failure_examples_response,
    build_synonym_second_chance_response,
    build_review_prompt,
    generate_stored_vocabulary_examples,
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


def test_build_sentence_failure_examples_response_includes_examples():
    review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello",
    )

    response = build_sentence_failure_examples_response(
        review,
        'You used "bonjour" like a noun here, but it is a greeting.',
        [
            "Bonjour, comment allez-vous ?",
            "Je dis bonjour en entrant.",
            "Elle a murmure bonjour.",
            "Ils passent dire bonjour.",
            "Un bonjour chaleureux suffit parfois.",
        ],
    )

    assert response == (
        'You used "bonjour" like a noun here, but it is a greeting.\n\n'
        'We will move on for now.\n\n'
        'Here are 5 example sentences using "bonjour" correctly:\n'
        "1. Bonjour, comment allez-vous ?\n"
        "2. Je dis bonjour en entrant.\n"
        "3. Elle a murmure bonjour.\n"
        "4. Ils passent dire bonjour.\n"
        "5. Un bonjour chaleureux suffit parfois."
    )


def test_generate_stored_vocabulary_examples_returns_three_sentences(monkeypatch):
    def _fake_call_text_with_schema(prompt, user_text, response_model, response_name):
        assert response_model is VocabularyStoredExamples
        assert response_name == "stored_vocabulary_sentence_examples"
        assert "Generate exactly 3 short, natural French example sentences" in prompt
        return VocabularyStoredExamples(
            sentences=[
                "Bonjour, comment allez-vous ?",
                "Je passe dire bonjour avant le travail.",
                "Elle aime dire bonjour avec un grand sourire.",
            ]
        )

    monkeypatch.setattr("src.vocabulary_review._call_text_with_schema", _fake_call_text_with_schema)

    assert generate_stored_vocabulary_examples("bonjour", "hello") == [
        "Bonjour, comment allez-vous ?",
        "Je passe dire bonjour avant le travail.",
        "Elle aime dire bonjour avec un grand sourire.",
    ]


def test_append_vocabulary_examples_to_description_adds_examples_block():
    merged = append_vocabulary_examples_to_description(
        "hello; a common greeting in French.",
        [
            "Bonjour, comment allez-vous ?",
            "Je passe dire bonjour avant le travail.",
            "Elle aime dire bonjour avec un grand sourire.",
        ],
    )

    assert merged == (
        "hello; a common greeting in French.\n"
        "Examples:\n"
        "1. Bonjour, comment allez-vous ?\n"
        "2. Je passe dire bonjour avant le travail.\n"
        "3. Elle aime dire bonjour avec un grand sourire."
    )


def test_build_synonym_second_chance_response_does_not_reveal_target_word():
    review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello",
    )

    response = build_synonym_second_chance_response(
        review,
        "salut",
        "Salut is more informal",
    )

    assert response == (
        'Yes, "salut" also fits, but I am looking for a different word. '
        "Salut is more informal. Please try again."
    )
