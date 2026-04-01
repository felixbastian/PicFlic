from src.vocabulary_review import is_review_answer_correct, normalize_review_text


def test_normalize_review_text_unifies_curly_apostrophes():
    assert normalize_review_text("l’abri") == "l'abri"


def test_is_review_answer_correct_accepts_curly_apostrophe_variant():
    assert is_review_answer_correct("l'abri", "l’abri") is True
