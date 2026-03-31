"""Dedicated vocabulary review agent."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph
from typing_extensions import NotRequired, TypedDict

from ..db import PostgresDatabase
from ..models import DueVocabularyReview, VocabularyReviewResult
from ..vocabulary_review import build_review_response, is_review_answer_correct, is_shelf_request

logger = logging.getLogger(__name__)


class _VocabularyState(TypedDict):
    telegram_user_id: int
    answer_text: str
    has_pending_review: NotRequired[bool]
    pending_review: NotRequired[DueVocabularyReview | None]
    correct: NotRequired[bool]
    shelved: NotRequired[bool]
    review_result: NotRequired[VocabularyReviewResult]
    response: NotRequired[str]


class _VocabularyContext(TypedDict):
    db: PostgresDatabase


def _load_review(state: _VocabularyState, runtime: Any) -> dict:
    return {"answer_text": state["answer_text"].strip()}


async def _fetch_pending_review(state: _VocabularyState, runtime: Any) -> dict:
    db: PostgresDatabase = runtime.context["db"]
    pending_review = await db.get_pending_vocabulary_review(state["telegram_user_id"])
    if pending_review is None:
        logger.info(
            "No pending vocabulary review for Telegram user",
            extra={"event": "vocabulary_review_none_pending", "telegram_user_id": state["telegram_user_id"]},
        )
        return {"has_pending_review": False, "pending_review": None}
    return {"has_pending_review": True, "pending_review": pending_review}


def _next_step(state: _VocabularyState) -> str:
    if state["has_pending_review"]:
        return "evaluate_review"
    return "build_no_pending_response"


def _evaluate_review(state: _VocabularyState, runtime: Any) -> dict:
    pending_review = state["pending_review"]
    answer_text = state["answer_text"]
    shelved = is_shelf_request(answer_text)
    correct = False
    if not shelved:
        correct = is_review_answer_correct(pending_review.french_word, answer_text)
    return {"shelved": shelved, "correct": correct}


async def _persist_review_result(state: _VocabularyState, runtime: Any) -> dict:
    db: PostgresDatabase = runtime.context["db"]
    pending_review = state["pending_review"]
    review_result = await db.record_vocabulary_review_result(
        pending_review.vocabulary_id,
        correct=state["correct"],
        shelved=state["shelved"],
    )
    return {"review_result": review_result}


def _build_review_response(state: _VocabularyState, runtime: Any) -> dict:
    pending_review = state["pending_review"]
    review_result = state["review_result"]
    response = build_review_response(pending_review, review_result)
    logger.info(
        "Built vocabulary review response",
        extra={
            "event": "vocabulary_review_response_built",
            "vocabulary_id": pending_review.vocabulary_id,
            "correct": review_result.correct,
            "shelved": review_result.shelved,
        },
    )
    return {"response": response}


def _build_no_pending_response(state: _VocabularyState, runtime: Any) -> dict:
    return {"response": "No vocabulary review is waiting right now. Use the main bot to save new words first."}


class VocabularyAgent:
    """Agent responsible for vocabulary review answer processing."""

    def __init__(self) -> None:
        self._review_graph = self._build_review_graph()

    def _build_review_graph(self) -> StateGraph[_VocabularyState, _VocabularyContext, _VocabularyState, dict]:
        graph = StateGraph(state_schema=_VocabularyState, context_schema=_VocabularyContext)
        graph.add_node("load_review", _load_review)
        graph.add_node("fetch_pending_review", _fetch_pending_review)
        graph.add_node("evaluate_review", _evaluate_review)
        graph.add_node("persist_review_result", _persist_review_result)
        graph.add_node("build_review_response", _build_review_response)
        graph.add_node("build_no_pending_response", _build_no_pending_response)
        graph.add_edge("load_review", "fetch_pending_review")
        graph.add_conditional_edges(
            "fetch_pending_review",
            _next_step,
            {
                "evaluate_review": "evaluate_review",
                "build_no_pending_response": "build_no_pending_response",
            },
        )
        graph.add_edge("evaluate_review", "persist_review_result")
        graph.add_edge("persist_review_result", "build_review_response")
        graph.set_entry_point("load_review")
        graph.set_finish_point("build_review_response")
        graph.set_finish_point("build_no_pending_response")
        return graph.compile()

    async def process_review_answer(
        self,
        telegram_user_id: int,
        answer_text: str,
        db: PostgresDatabase,
    ) -> dict:
        return await self._review_graph.ainvoke(
            {
                "telegram_user_id": telegram_user_id,
                "answer_text": answer_text,
            },
            context={"db": db},
        )


__all__ = ["VocabularyAgent"]
