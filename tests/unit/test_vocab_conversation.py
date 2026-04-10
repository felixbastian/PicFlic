import asyncio
from datetime import datetime
from types import SimpleNamespace

from src.models import (
    ConversationVocabularyCandidate,
    VocabularyConversationEligibleUser,
    VocabularyConversationFeedback,
    VocabularyConversationOpeningPlan,
    VocabularyConversationReply,
    VocabularyConversationSession,
    VocabularyConversationTurn,
)
from src.vocab_bot.conversation import VocabularyConversationTrainer, VocabularyUsageTracker


class _FakeTelegramBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text})


class _FakeApplication:
    def __init__(self) -> None:
        self.bot = _FakeTelegramBot()


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class _FakeConversationDb:
    def __init__(self) -> None:
        self.users = [
            VocabularyConversationEligibleUser(user_id="user-123", telegram_user_id=42),
        ]
        self.candidates = [
            ConversationVocabularyCandidate(
                vocabulary_id="vocab-1",
                user_id="user-123",
                french_word="habitude",
                english_description="habit",
                number_of_usages_by_conversation_trainer=0,
                finished=False,
            ),
            ConversationVocabularyCandidate(
                vocabulary_id="vocab-2",
                user_id="user-123",
                french_word="projet",
                english_description="project",
                number_of_usages_by_conversation_trainer=1,
                finished=False,
            ),
        ]
        self.active_session = VocabularyConversationSession(
            conversation_id="conversation-1",
            user_id="user-123",
            telegram_user_id=42,
            story_type="ask_me_something",
            status="active",
            user_turn_count=1,
            max_user_turns=5,
            turn_count=2,
            selected_vocabulary_ids=["vocab-1", "vocab-2"],
            last_activity_at=datetime(2026, 4, 10, 10, 0, 0),
            timeout_at=datetime(2026, 4, 11, 22, 0, 0),
            completed_at=None,
        )
        self.turns = [
            VocabularyConversationTurn(
                conversation_turn_id="turn-1",
                conversation_id="conversation-1",
                turn_index=1,
                turn_type="bot_opening",
                text="Salut ! Quelle habitude aimerais-tu changer cette annee ?",
                used_vocabulary_ids=["vocab-1"],
                created_at=datetime(2026, 4, 10, 10, 0, 0),
            ),
            VocabularyConversationTurn(
                conversation_turn_id="turn-2",
                conversation_id="conversation-1",
                turn_index=2,
                turn_type="user_reply",
                text="Je veux changer ma routine du matin.",
                used_vocabulary_ids=[],
                created_at=datetime(2026, 4, 10, 10, 1, 0),
            ),
        ]
        self.created_sessions: list[dict] = []
        self.increment_usage_calls: list[list[str]] = []
        self.feedback_calls: list[str] = []
        self.bot_reply_calls: list[dict] = []
        self.expire_calls = 0
        self.timed_out_ids: list[str] = []

    async def expire_stale_vocabulary_conversations(self) -> int:
        self.expire_calls += 1
        return 0

    async def list_users_ready_for_vocabulary_conversations(self, limit: int = 100):
        return self.users[:limit]

    async def list_vocabulary_conversation_candidates(self, user_id: str, limit: int = 50):
        return self.candidates[:limit]

    async def create_vocabulary_conversation_session(
        self,
        user_id: str,
        telegram_user_id: int,
        story_type: str,
        selected_vocabulary_ids,
        opening_message: str,
        *,
        opening_used_vocabulary_ids=(),
        max_user_turns: int = 5,
    ) -> str:
        self.created_sessions.append(
            {
                "user_id": user_id,
                "telegram_user_id": telegram_user_id,
                "story_type": story_type,
                "selected_vocabulary_ids": list(selected_vocabulary_ids),
                "opening_message": opening_message,
                "opening_used_vocabulary_ids": list(opening_used_vocabulary_ids),
                "max_user_turns": max_user_turns,
            }
        )
        return "conversation-1"

    async def increment_vocabulary_conversation_trainer_usage(self, vocabulary_ids):
        self.increment_usage_calls.append(list(vocabulary_ids))

    async def mark_vocabulary_conversation_timed_out(self, conversation_id: str) -> None:
        self.timed_out_ids.append(conversation_id)

    async def get_active_vocabulary_conversation(self, telegram_user_id: int):
        return self.active_session if telegram_user_id == self.active_session.telegram_user_id else None

    async def list_vocabulary_words_by_ids(self, user_id: str, vocabulary_ids):
        selected = set(vocabulary_ids)
        return [candidate for candidate in self.candidates if candidate.vocabulary_id in selected]

    async def record_vocabulary_conversation_user_reply(self, conversation_id: str, text: str):
        self.active_session = self.active_session.model_copy(
            update={
                "user_turn_count": self.active_session.user_turn_count + 1,
                "turn_count": self.active_session.turn_count + 1,
            }
        )
        self.turns.append(
            VocabularyConversationTurn(
                conversation_turn_id=f"turn-{len(self.turns) + 1}",
                conversation_id=conversation_id,
                turn_index=self.active_session.turn_count,
                turn_type="user_reply",
                text=text,
                used_vocabulary_ids=[],
                created_at=datetime(2026, 4, 10, 10, 2, 0),
            )
        )
        return self.active_session

    async def list_vocabulary_conversation_turns(self, conversation_id: str):
        return list(self.turns)

    async def record_vocabulary_conversation_feedback(self, conversation_id: str, text: str):
        self.feedback_calls.append(text)
        self.active_session = self.active_session.model_copy(
            update={"turn_count": self.active_session.turn_count + 1}
        )
        self.turns.append(
            VocabularyConversationTurn(
                conversation_turn_id=f"turn-{len(self.turns) + 1}",
                conversation_id=conversation_id,
                turn_index=self.active_session.turn_count,
                turn_type="bot_feedback",
                text=text,
                used_vocabulary_ids=[],
                created_at=datetime(2026, 4, 10, 10, 3, 0),
            )
        )
        return self.active_session

    async def record_vocabulary_conversation_bot_reply(
        self,
        conversation_id: str,
        text: str,
        *,
        used_vocabulary_ids=(),
        complete: bool = False,
    ):
        self.bot_reply_calls.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                "used_vocabulary_ids": list(used_vocabulary_ids),
                "complete": complete,
            }
        )
        self.active_session = self.active_session.model_copy(
            update={
                "turn_count": self.active_session.turn_count + 1,
                "status": "completed" if complete else self.active_session.status,
            }
        )
        self.turns.append(
            VocabularyConversationTurn(
                conversation_turn_id=f"turn-{len(self.turns) + 1}",
                conversation_id=conversation_id,
                turn_index=self.active_session.turn_count,
                turn_type="bot_closing" if complete else "bot_reply",
                text=text,
                used_vocabulary_ids=list(used_vocabulary_ids),
                created_at=datetime(2026, 4, 10, 10, 4, 0),
            )
        )
        return self.active_session


def test_usage_tracker_counts_selected_word_once_per_message():
    tracker = VocabularyUsageTracker()
    selected_words = [
        ConversationVocabularyCandidate(
            vocabulary_id="vocab-1",
            user_id="user-123",
            french_word="projet",
            english_description="project",
            number_of_usages_by_conversation_trainer=0,
            finished=False,
        ),
        ConversationVocabularyCandidate(
            vocabulary_id="vocab-2",
            user_id="user-123",
            french_word="habitude",
            english_description="habit",
            number_of_usages_by_conversation_trainer=0,
            finished=False,
        ),
    ]

    used_ids = tracker.extract_used_vocabulary_ids(
        selected_words,
        "Ton projet avance bien, et ce projet semble tres clair.",
    )

    assert used_ids == ["vocab-1"]


def test_dispatch_daily_conversations_sends_opening_and_increments_usage(monkeypatch):
    trainer = VocabularyConversationTrainer()
    application = _FakeApplication()
    db = _FakeConversationDb()

    monkeypatch.setattr(
        trainer.selector,
        "build_opening_plan",
        lambda candidates, story_type="ask_me_something": VocabularyConversationOpeningPlan(
            story_type="ask_me_something",
            selected_vocabulary_ids=["vocab-1", "vocab-2"],
            opening_message="Salut ! Quelle habitude aimerais-tu changer cette annee ?",
        ),
    )

    started_count = asyncio.run(trainer.dispatch_daily_conversations(application, db))

    assert started_count == 1
    assert application.bot.sent_messages == [
        {
            "chat_id": 42,
            "text": "Salut ! Quelle habitude aimerais-tu changer cette annee ?",
        }
    ]
    assert db.created_sessions == [
        {
            "user_id": "user-123",
            "telegram_user_id": 42,
            "story_type": "ask_me_something",
            "selected_vocabulary_ids": ["vocab-1", "vocab-2"],
            "opening_message": "Salut ! Quelle habitude aimerais-tu changer cette annee ?",
            "opening_used_vocabulary_ids": ["vocab-1"],
            "max_user_turns": 5,
        }
    ]
    assert db.increment_usage_calls == [["vocab-1"]]


def test_handle_active_conversation_message_sends_feedback_then_reply(monkeypatch):
    trainer = VocabularyConversationTrainer()
    db = _FakeConversationDb()
    message = _FakeMessage("Je pense changer mon routine demain.")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=message,
    )

    monkeypatch.setattr(
        trainer.feedback_generator,
        "generate_feedback",
        lambda session, selected_words, transcript, user_reply: VocabularyConversationFeedback(
            should_send_feedback=True,
            feedback_message='Try "ma routine" instead of "mon routine".',
        ),
    )
    monkeypatch.setattr(
        trainer.response_generator,
        "generate_reply",
        lambda session, selected_words, transcript, user_reply, is_final_turn=False: VocabularyConversationReply(
            reply_message="C'est un beau projet. Qu'est-ce qui te motive le plus ?"
        ),
    )

    handled = asyncio.run(trainer.handle_active_conversation_message(update, db))

    assert handled is True
    assert message.replies == [
        'Try "ma routine" instead of "mon routine".',
        "C'est un beau projet. Qu'est-ce qui te motive le plus ?",
    ]
    assert db.feedback_calls == ['Try "ma routine" instead of "mon routine".']
    assert db.bot_reply_calls == [
        {
            "conversation_id": "conversation-1",
            "text": "C'est un beau projet. Qu'est-ce qui te motive le plus ?",
            "used_vocabulary_ids": ["vocab-2"],
            "complete": False,
        }
    ]
    assert db.increment_usage_calls == [["vocab-2"]]
