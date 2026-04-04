import asyncio

from src.agents import VocabularyAgent
from src.models import DueVocabularyReview, VocabularyReviewResult, VocabularySentenceEvaluation


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
        self.increment_sentence_attempt_calls: list[str] = []
        self.clear_sentence_prompt_calls: list[str] = []
        self.mark_used_in_sentence_calls: list[str] = []

    async def get_pending_vocabulary_review(self, telegram_user_id: int):
        return self.pending_review

    async def record_vocabulary_review_result(
        self,
        vocabulary_id: str,
        *,
        correct: bool = False,
        shelved: bool = False,
        request_sentence_practice: bool = False,
    ):
        self.record_calls.append(
            {
                "vocabulary_id": vocabulary_id,
                "correct": correct,
                "shelved": shelved,
                "request_sentence_practice": request_sentence_practice,
            }
        )
        if correct:
            return VocabularyReviewResult(
                vocabulary_id=vocabulary_id,
                user_id="user-123",
                french_word="bonjour",
                correct=True,
                shelved=False,
                finished=False,
                current_review_stage="three_days",
                next_review_at=None,
                awaiting_sentence=request_sentence_practice,
            )
        return VocabularyReviewResult(
            vocabulary_id=vocabulary_id,
            user_id="user-123",
            french_word="bonjour",
            correct=False,
            shelved=shelved,
            finished=False,
            current_review_stage="day",
            next_review_at=None,
            awaiting_sentence=False,
        )

    async def increment_vocabulary_sentence_attempts(self, vocabulary_id: str) -> int:
        self.increment_sentence_attempt_calls.append(vocabulary_id)
        return 1

    async def clear_vocabulary_sentence_prompt(self, vocabulary_id: str) -> None:
        self.clear_sentence_prompt_calls.append(vocabulary_id)

    async def mark_vocabulary_used_in_sentence(self, vocabulary_id: str) -> None:
        self.mark_used_in_sentence_calls.append(vocabulary_id)


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
            "request_sentence_practice": False,
        }
    ]
    assert result["review_result"].correct is False
    assert result["dispatch_next_due_review"] is True
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
    assert result["dispatch_next_due_review"] is False
    assert result["response"] == (
        'Yes, "salut" also fits, but I am looking for "bonjour". '
        "Salut is more informal. Please try again."
    )


def test_process_review_answer_can_queue_sentence_practice(monkeypatch):
    db = _FakePostgresDatabase()
    agent = VocabularyAgent()

    monkeypatch.setattr(
        "src.agents.vocabulary_agent.should_prompt_for_sentence_practice",
        lambda review: True,
    )

    result = asyncio.run(agent.process_review_answer(42, "bonjour", db))

    assert db.record_calls == [
        {
            "vocabulary_id": "vocab-1",
            "correct": True,
            "shelved": False,
            "request_sentence_practice": True,
        }
    ]
    assert result["review_result"].awaiting_sentence is True
    assert result["dispatch_next_due_review"] is False
    assert result["response"] == (
        'Correct. The French word is "bonjour". I will ask you again in 3 days.\n\n'
        'Write one short French sentence using "bonjour". Reply \'p\' or \'pass\' to skip this part.'
    )


def test_process_review_answer_retries_sentence_practice_when_usage_is_wrong(monkeypatch):
    db = _FakePostgresDatabase()
    db.pending_review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello",
        current_review_stage="three_days",
        next_review_at="2026-03-26T10:00:00",
        awaiting_sentence=True,
        sentence_attempts=0,
    )
    agent = VocabularyAgent()

    monkeypatch.setattr(
        "src.agents.vocabulary_agent.evaluate_vocabulary_sentence",
        lambda review, answer: VocabularySentenceEvaluation(
            acceptable=False,
            corrected_sentence=None,
            feedback='You used "bonjour" like a noun here, but it is a greeting.',
        ),
    )

    result = asyncio.run(agent.process_review_answer(42, "Le bonjour est grand.", db))

    assert db.increment_sentence_attempt_calls == ["vocab-1"]
    assert db.clear_sentence_prompt_calls == []
    assert db.mark_used_in_sentence_calls == []
    assert result["dispatch_next_due_review"] is False
    assert result["response"] == (
        'You used "bonjour" like a noun here, but it is a greeting.\n\n'
        'Try one more short French sentence using "bonjour". Reply \'p\' or \'pass\' to skip this part.'
    )


def test_process_review_answer_marks_vocab_used_in_sentence_after_acceptable_sentence(monkeypatch):
    db = _FakePostgresDatabase()
    db.pending_review = DueVocabularyReview(
        vocabulary_id="vocab-1",
        user_id="user-123",
        telegram_user_id=42,
        french_word="bonjour",
        english_description="hello",
        current_review_stage="three_days",
        next_review_at="2026-03-26T10:00:00",
        awaiting_sentence=True,
        sentence_attempts=1,
    )
    agent = VocabularyAgent()

    monkeypatch.setattr(
        "src.agents.vocabulary_agent.evaluate_vocabulary_sentence",
        lambda review, answer: VocabularySentenceEvaluation(
            acceptable=True,
            corrected_sentence="Je dis bonjour a mes voisins chaque matin.",
            feedback='Nice. The sentence works.',
        ),
    )

    result = asyncio.run(agent.process_review_answer(42, "Je dit bonjour a mes voisins.", db))

    assert db.mark_used_in_sentence_calls == ["vocab-1"]
    assert db.clear_sentence_prompt_calls == []
    assert result["dispatch_next_due_review"] is True
    assert result["response"] == (
        'Nice. The sentence works.\n'
        'Corrected sentence: "Je dis bonjour a mes voisins chaque matin."'
    )
