"""Conversation trainer orchestration for the separate vocabulary bot."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from typing import Sequence

from telegram import Update
from telegram.ext import Application

from ..db import PostgresDatabase
from ..models import (
    ConversationVocabularyCandidate,
    VocabularyConversationFeedback,
    VocabularyConversationOpeningPlan,
    VocabularyConversationReply,
    VocabularyConversationSession,
    VocabularyConversationTurn,
)
from ..query_utils import call_text_with_schema
from ..vocabulary_review import is_pass_request

logger = logging.getLogger(__name__)

_DEFAULT_STORY_TYPE = "ask_me_something"
_ACTIVE_STORY_TYPES = {_DEFAULT_STORY_TYPE}
_DEFAULT_CANDIDATE_LIMIT = 50
_DEFAULT_SESSION_LIMIT = 100
_DEFAULT_MAX_USER_TURNS = 5
_MAX_SELECTED_VOCABULARY = 10
_PASS_CONVERSATION_CLOSE_MESSAGE = (
    "Okay, we can stop here for today. "
    "I enjoyed our chat. "
    "I'll start a new one when the next daily conversation is due."
)
_APOSTROPHE_VARIANTS = {
    "\u2019": "'",
    "\u2018": "'",
    "\u02bc": "'",
    "\u2032": "'",
    "\u00b4": "'",
    "`": "'",
}


def _normalize_for_vocab_match(value: str) -> str:
    lowered = value.strip().lower()
    for variant, replacement in _APOSTROPHE_VARIANTS.items():
        lowered = lowered.replace(variant, replacement)
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = re.sub(r"[^a-z0-9]+", " ", without_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_conversation_friendly_word(candidate: ConversationVocabularyCandidate) -> bool:
    normalized = candidate.french_word.strip()
    if not normalized:
        return False
    if any(separator in normalized for separator in (",", ";", "/", "|")):
        return False
    return len(normalized.split()) <= 4


def _build_selected_word_lines(
    selected_words: Sequence[ConversationVocabularyCandidate],
    transcript: Sequence[VocabularyConversationTurn],
) -> str:
    usage_counts = Counter(
        vocabulary_id
        for turn in transcript
        for vocabulary_id in turn.used_vocabulary_ids
    )
    lines = []
    for word in selected_words:
        lines.append(
            f"- id={word.vocabulary_id} | french={word.french_word} | english={word.english_description} | "
            f"global_trainer_usage={word.number_of_usages_by_conversation_trainer} | "
            f"used_in_this_conversation={usage_counts.get(word.vocabulary_id, 0)}"
        )
    return "\n".join(lines) if lines else "- none"


def _build_transcript_lines(transcript: Sequence[VocabularyConversationTurn]) -> str:
    if not transcript:
        return "- none"
    return "\n".join(f"{turn.turn_index}. {turn.turn_type}: {turn.text}" for turn in transcript)


class VocabularyUsageTracker:
    """Checks which selected vocabulary items truly appeared in a bot message."""

    def extract_used_vocabulary_ids(
        self,
        selected_words: Sequence[ConversationVocabularyCandidate],
        message_text: str,
    ) -> list[str]:
        normalized_message = f" {_normalize_for_vocab_match(message_text)} "
        if not normalized_message.strip():
            return []

        used_ids: list[str] = []
        for word in selected_words:
            normalized_word = _normalize_for_vocab_match(word.french_word)
            if not normalized_word:
                continue
            if f" {normalized_word} " in normalized_message:
                used_ids.append(word.vocabulary_id)
        return list(dict.fromkeys(used_ids))


class VocabularySelector:
    """Chooses a conversation-friendly subset of stored vocabulary items."""

    def build_opening_plan(
        self,
        candidates: Sequence[ConversationVocabularyCandidate],
        *,
        story_type: str = _DEFAULT_STORY_TYPE,
    ) -> VocabularyConversationOpeningPlan:
        prompt = (
            "You design the opening of a daily French vocabulary conversation in Telegram. "
            "The bot should feel natural, motivating, and personal, not like a quiz. "
            "Supported story types are ask_me_something and tell_me_something, but tell_me_something is inactive "
            "and must never be chosen right now. "
            "Choose a subset of 5 to 10 vocabulary items when possible. "
            "If fewer than 5 candidates fit naturally, choose the best smaller subset. "
            "Prefer lower prior trainer-usage counts, avoid awkward items with punctuation or overly technical wording "
            "when better options exist, and choose words that can fit one coherent personal or reflective conversation. "
            "Write one opening message for story_type='ask_me_something'. "
            "The opening should be a natural question or invitation to talk, mostly in simple French, and should use "
            "1 to 3 of the selected French words exactly as written when that feels natural. "
            "Do not dump a vocabulary list. Return only the structured result."
        )
        user_text = (
            f"Requested story_type: {story_type}\n"
            "Candidate vocabulary:\n"
            + "\n".join(
                (
                    f"- id={candidate.vocabulary_id} | french={candidate.french_word} | "
                    f"english={candidate.english_description} | "
                    f"trainer_usage={candidate.number_of_usages_by_conversation_trainer} | "
                    f"finished={candidate.finished}"
                )
                for candidate in candidates
            )
        )

        try:
            plan = call_text_with_schema(
                prompt,
                user_text,
                VocabularyConversationOpeningPlan,
                "vocabulary_conversation_opening_plan",
            )
            return self._sanitize_plan(plan, candidates)
        except Exception:
            logger.exception("Failed to generate vocabulary conversation opening plan")
            return self._fallback_plan(candidates)

    def _sanitize_plan(
        self,
        plan: VocabularyConversationOpeningPlan,
        candidates: Sequence[ConversationVocabularyCandidate],
    ) -> VocabularyConversationOpeningPlan:
        candidate_ids = {candidate.vocabulary_id for candidate in candidates}
        selected_ids = [
            vocabulary_id
            for vocabulary_id in plan.selected_vocabulary_ids
            if vocabulary_id in candidate_ids
        ]
        selected_ids = list(dict.fromkeys(selected_ids))[:_MAX_SELECTED_VOCABULARY]
        if not selected_ids:
            return self._fallback_plan(candidates)

        story_type = plan.story_type if plan.story_type in _ACTIVE_STORY_TYPES else _DEFAULT_STORY_TYPE
        opening_message = plan.opening_message.strip()
        if not opening_message:
            return self._fallback_plan(candidates, selected_ids=selected_ids)

        return VocabularyConversationOpeningPlan(
            story_type=story_type,
            selected_vocabulary_ids=selected_ids,
            opening_message=opening_message,
        )

    def _fallback_plan(
        self,
        candidates: Sequence[ConversationVocabularyCandidate],
        *,
        selected_ids: Sequence[str] | None = None,
    ) -> VocabularyConversationOpeningPlan:
        fallback_candidates = [candidate for candidate in candidates if _is_conversation_friendly_word(candidate)]
        if not fallback_candidates:
            fallback_candidates = list(candidates)
        resolved_ids = list(selected_ids) if selected_ids else [
            candidate.vocabulary_id
            for candidate in fallback_candidates[: min(len(fallback_candidates), 5)]
        ]
        if not resolved_ids and candidates:
            resolved_ids = [candidates[0].vocabulary_id]

        return VocabularyConversationOpeningPlan(
            story_type=_DEFAULT_STORY_TYPE,
            selected_vocabulary_ids=resolved_ids,
            opening_message=(
                "Salut ! J'ai envie de parler un peu avec toi aujourd'hui. "
                "Qu'est-ce qui t'occupe le plus en ce moment ?"
            ),
        )


class FeedbackGenerator:
    """Builds short correction messages for meaningful user mistakes."""

    def generate_feedback(
        self,
        session: VocabularyConversationSession,
        selected_words: Sequence[ConversationVocabularyCandidate],
        transcript: Sequence[VocabularyConversationTurn],
        user_reply: str,
    ) -> VocabularyConversationFeedback:
        prompt = (
            "You are a concise French writing coach inside a Telegram conversation trainer. "
            "Review the user's latest reply. "
            "Give feedback only when there is a meaningful grammar mistake, vocabulary mistake, clearly unnatural "
            "phrasing, or incorrect use of a selected vocabulary word that was introduced by the bot. "
            "Ignore tiny issues that do not matter. "
            "Be encouraging, brief, and easy to understand. "
            "Write feedback in English, but include short corrected French snippets when helpful. "
            "Do not continue the conversation here. "
            "If there is no meaningful issue, return should_send_feedback=false and feedback_message=null. "
            "Return only the structured result."
        )
        user_text = (
            f"Story type: {session.story_type}\n"
            f"User turns completed so far: {session.user_turn_count}/{session.max_user_turns}\n"
            f"Latest user reply: {user_reply}\n"
            "Selected vocabulary:\n"
            f"{_build_selected_word_lines(selected_words, transcript)}\n"
            "Transcript:\n"
            f"{_build_transcript_lines(transcript)}"
        )
        try:
            return call_text_with_schema(
                prompt,
                user_text,
                VocabularyConversationFeedback,
                "vocabulary_conversation_feedback",
            )
        except Exception:
            logger.exception("Failed to generate vocabulary conversation feedback")
            return VocabularyConversationFeedback(should_send_feedback=False, feedback_message=None)


class ResponseGenerator:
    """Generates the bot's next conversational reply."""

    def generate_reply(
        self,
        session: VocabularyConversationSession,
        selected_words: Sequence[ConversationVocabularyCandidate],
        transcript: Sequence[VocabularyConversationTurn],
        user_reply: str,
        *,
        core_goal_completed: bool,
    ) -> VocabularyConversationReply:
        prompt = (
            "You continue a short daily Telegram conversation that helps the user learn French vocabulary in context. "
            "The bot should feel proactive, natural, and motivating, not like a quiz. "
            "Continue the conversation based on the transcript and the user's latest reply. "
            "Write mostly in simple natural French. "
            "Use up to 1 or 2 selected French vocabulary items exactly as written when they fit naturally, and spread "
            "them across the conversation instead of forcing all of them at once. "
            "Prefer selected words that have been used less often in this conversation. "
            "If the core daily goal is already complete, it is okay to use no selected word when forcing one would "
            "sound awkward. "
            "Keep the reply to 2 to 4 short sentences. "
            "If the core daily goal is not complete yet, ask a relevant follow-up question or make a small natural "
            "comment that invites the user to continue. "
            "If the core daily goal is already complete, keep the conversation lively and responsive if the user "
            "wants to continue, and feel free to ask another relevant question or explore the topic further. "
            "Do not end the conversation just because the core goal was reached. "
            "Do not provide grammar correction here. Return only the structured result."
        )
        user_text = (
            f"Story type: {session.story_type}\n"
            f"Latest user reply: {user_reply}\n"
            f"Core daily goal completed: {'yes' if core_goal_completed else 'no'}\n"
            f"User turns completed so far: {session.user_turn_count}/{session.max_user_turns}\n"
            "Selected vocabulary:\n"
            f"{_build_selected_word_lines(selected_words, transcript)}\n"
            "Transcript:\n"
            f"{_build_transcript_lines(transcript)}"
        )
        try:
            reply = call_text_with_schema(
                prompt,
                user_text,
                VocabularyConversationReply,
                "vocabulary_conversation_reply",
            )
            if reply.reply_message.strip():
                return reply
        except Exception:
            logger.exception("Failed to generate vocabulary conversation reply")
        return self._fallback_reply(core_goal_completed=core_goal_completed)

    def _fallback_reply(self, *, core_goal_completed: bool) -> VocabularyConversationReply:
        if core_goal_completed:
            return VocabularyConversationReply(
                reply_message=(
                    "C'est interessant. "
                    "Et qu'est-ce qui compte le plus pour toi dans tout ca ?"
                )
            )
        return VocabularyConversationReply(
            reply_message=(
                "Merci pour ta réponse. "
                "Dis-m'en un peu plus si tu veux."
            )
        )


class VocabularyConversationTrainer:
    """Coordinates proactive vocabulary conversations in the separate Telegram bot."""

    def __init__(self) -> None:
        self.selector = VocabularySelector()
        self.feedback_generator = FeedbackGenerator()
        self.response_generator = ResponseGenerator()
        self.usage_tracker = VocabularyUsageTracker()

    async def dispatch_daily_conversations(
        self,
        application: Application,
        postgres_db: PostgresDatabase,
        limit: int = _DEFAULT_SESSION_LIMIT,
    ) -> int:
        """Start one new vocabulary conversation for each eligible user."""
        await postgres_db.expire_stale_vocabulary_conversations()
        started_count = 0
        users = await postgres_db.list_users_ready_for_vocabulary_conversations(limit=limit)
        for user in users:
            if await self._start_conversation_for_user(application, postgres_db, user.user_id, user.telegram_user_id):
                started_count += 1
        return started_count

    async def handle_active_conversation_message(
        self,
        update: Update,
        postgres_db: PostgresDatabase,
    ) -> bool:
        """Process a user message when a trainer conversation is active."""
        message = update.message
        effective_user = update.effective_user
        if message is None or effective_user is None:
            return False

        await postgres_db.expire_stale_vocabulary_conversations()
        session = await postgres_db.get_active_vocabulary_conversation(effective_user.id)
        if session is None:
            return False

        user_reply = (message.text or "").strip()
        session = await postgres_db.record_vocabulary_conversation_user_reply(
            session.conversation_id,
            user_reply,
        )

        if is_pass_request(user_reply):
            await postgres_db.record_vocabulary_conversation_bot_reply(
                session.conversation_id,
                _PASS_CONVERSATION_CLOSE_MESSAGE,
                used_vocabulary_ids=(),
                complete=True,
            )
            try:
                await message.reply_text(_PASS_CONVERSATION_CLOSE_MESSAGE)
            except Exception:
                await postgres_db.mark_vocabulary_conversation_timed_out(session.conversation_id)
                raise
            return True

        selected_words = await postgres_db.list_vocabulary_words_by_ids(
            session.user_id,
            session.selected_vocabulary_ids,
        )
        transcript = await postgres_db.list_vocabulary_conversation_turns(session.conversation_id)

        feedback = self.feedback_generator.generate_feedback(session, selected_words, transcript, user_reply)
        if feedback.should_send_feedback and feedback.feedback_message:
            await postgres_db.record_vocabulary_conversation_feedback(
                session.conversation_id,
                feedback.feedback_message,
            )
            try:
                await message.reply_text(feedback.feedback_message)
            except Exception:
                await postgres_db.mark_vocabulary_conversation_timed_out(session.conversation_id)
                raise
            transcript = await postgres_db.list_vocabulary_conversation_turns(session.conversation_id)

        core_goal_completed = session.user_turn_count >= session.max_user_turns
        reply = self.response_generator.generate_reply(
            session,
            selected_words,
            transcript,
            user_reply,
            core_goal_completed=core_goal_completed,
        )
        used_vocabulary_ids = self.usage_tracker.extract_used_vocabulary_ids(
            selected_words,
            reply.reply_message,
        )
        await postgres_db.record_vocabulary_conversation_bot_reply(
            session.conversation_id,
            reply.reply_message,
            used_vocabulary_ids=used_vocabulary_ids,
            complete=False,
        )
        try:
            await message.reply_text(reply.reply_message)
        except Exception:
            await postgres_db.mark_vocabulary_conversation_timed_out(session.conversation_id)
            raise

        if used_vocabulary_ids:
            await postgres_db.increment_vocabulary_conversation_trainer_usage(used_vocabulary_ids)
        return True

    async def _start_conversation_for_user(
        self,
        application: Application,
        postgres_db: PostgresDatabase,
        user_id: str,
        telegram_user_id: int,
    ) -> bool:
        candidates = await postgres_db.list_vocabulary_conversation_candidates(
            user_id,
            limit=_DEFAULT_CANDIDATE_LIMIT,
        )
        if not candidates:
            return False

        opening_plan = self.selector.build_opening_plan(candidates, story_type=_DEFAULT_STORY_TYPE)
        selected_words = self._resolve_selected_words(candidates, opening_plan.selected_vocabulary_ids)
        if not selected_words:
            return False

        used_vocabulary_ids = self.usage_tracker.extract_used_vocabulary_ids(
            selected_words,
            opening_plan.opening_message,
        )
        conversation_id = await postgres_db.create_vocabulary_conversation_session(
            user_id,
            telegram_user_id,
            opening_plan.story_type,
            [word.vocabulary_id for word in selected_words],
            opening_plan.opening_message,
            opening_used_vocabulary_ids=used_vocabulary_ids,
            max_user_turns=_DEFAULT_MAX_USER_TURNS,
        )
        try:
            await application.bot.send_message(
                chat_id=telegram_user_id,
                text=opening_plan.opening_message,
            )
        except Exception:
            await postgres_db.mark_vocabulary_conversation_timed_out(conversation_id)
            logger.exception(
                "Failed to send vocabulary conversation opening",
                extra={"event": "vocabulary_conversation_opening_send_failed", "user_id": user_id},
            )
            return False

        if used_vocabulary_ids:
            await postgres_db.increment_vocabulary_conversation_trainer_usage(used_vocabulary_ids)

        logger.info(
            "Started vocabulary conversation",
            extra={
                "event": "vocabulary_conversation_started",
                "user_id": user_id,
                "conversation_id": conversation_id,
                "story_type": opening_plan.story_type,
                "selected_vocabulary_count": len(selected_words),
            },
        )
        return True

    def _resolve_selected_words(
        self,
        candidates: Sequence[ConversationVocabularyCandidate],
        selected_ids: Sequence[str],
    ) -> list[ConversationVocabularyCandidate]:
        candidate_map = {candidate.vocabulary_id: candidate for candidate in candidates}
        resolved: list[ConversationVocabularyCandidate] = []
        seen_ids: set[str] = set()
        for vocabulary_id in selected_ids:
            if vocabulary_id not in candidate_map or vocabulary_id in seen_ids:
                continue
            resolved.append(candidate_map[vocabulary_id])
            seen_ids.add(vocabulary_id)
        if resolved:
            return resolved[:_MAX_SELECTED_VOCABULARY]

        fallback_candidates = [candidate for candidate in candidates if _is_conversation_friendly_word(candidate)]
        if not fallback_candidates:
            fallback_candidates = list(candidates)
        return fallback_candidates[: min(len(fallback_candidates), 5)]


__all__ = ["VocabularyConversationTrainer"]
