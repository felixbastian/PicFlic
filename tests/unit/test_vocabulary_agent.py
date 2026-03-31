import asyncio

from src.agents import VocabularyAgent
from src.models import DueVocabularyReview, VocabularyReviewResult


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.pending_review = DueVocabularyReview(
            vocabulary_id="vocab-1",
            user_id="user-123",
            telegram_user_id=42,
            french_word="bonjour",
            english_description="hello",
            current_review_stage="day",
            next_review_at="2026-03-23T10:00:00",
        )
        self.record_calls: list[dict] = []

    async def get_pending_vocabulary_review(self, telegram_user_id: int):
        return self.pending_review

    async def record_vocabulary_review_result(self, vocabulary_id: str, correct: bool = False, shelved: bool = False):
        self.record_calls.append(
            {
                "vocabulary_id": vocabulary_id,
                "correct": correct,
                "shelved": shelved,
            }
        )
        return VocabularyReviewResult(
            vocabulary_id=vocabulary_id,
            user_id="user-123",
            french_word="bonjour",
            correct=correct,
            shelved=shelved,
            finished=False,
            current_review_stage="day",
            next_review_at=None,
        )


def test_process_review_answer_pass_shortcuts_to_wrong_answer(monkeypatch):
    db = _FakePostgresDatabase()
    agent = VocabularyAgent()

    def _unexpected_synonym_check(review, answer):
        raise AssertionError("pass answers should not trigger synonym checks")

    monkeypatch.setattr("src.agents.vocabulary_agent.maybe_build_synonym_second_chance", _unexpected_synonym_check)

    result = asyncio.run(agent.process_review_answer(42, "pass", db))

    assert db.record_calls == [
        {
            "vocabulary_id": "vocab-1",
            "correct": False,
            "shelved": False,
        }
    ]
    assert result["review_result"].correct is False
    assert result["response"] == 'Not quite. The correct word is "bonjour". I will ask you again tomorrow.'


def test_process_review_answer_gives_second_chance_for_synonym(monkeypatch):
    db = _FakePostgresDatabase()
    agent = VocabularyAgent()

    monkeypatch.setattr(
        "src.agents.vocabulary_agent.maybe_build_synonym_second_chance",
        lambda review, answer: (
            'Yes, "salut" also fits, but I am looking for "bonjour". '
            "Salut is more informal. Please try again."
        ),
    )

    result = asyncio.run(agent.process_review_answer(42, "salut", db))

    assert db.record_calls == []
    assert result.get("review_result") is None
    assert result["response"] == (
        'Yes, "salut" also fits, but I am looking for "bonjour". '
        "Salut is more informal. Please try again."
    )
