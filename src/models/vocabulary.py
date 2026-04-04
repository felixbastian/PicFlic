from __future__ import annotations

from datetime import datetime
from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field


VocabularyReviewStage = Literal["day", "three_days", "week", "month"]


class VocabularyWorkflowResult(BaseModel):
    """Structured result for vocabulary trainer interactions."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: Literal["vocabulary"] = Field(description="Identifies the vocabulary workflow.")
    assistant_reply: str = Field(
        description="The full assistant reply that should be sent back to the user."
    )
    store_vocabulary: bool = Field(
        description="Whether this interaction represents a new vocabulary entry that should be stored."
    )
    french_word: str | None = Field(
        description="The normalized French word or short expression to store, when applicable."
    )
    english_description: str | None = Field(
        description="The short English meaning and explanation to store, when applicable."
    )

    def to_dict(self) -> Dict[str, object]:
        return self.model_dump()


class DueVocabularyReview(BaseModel):
    """A vocabulary review that should be asked to the user."""

    model_config = ConfigDict(extra="forbid")

    vocabulary_id: str
    user_id: str
    telegram_user_id: int
    french_word: str
    english_description: str
    current_review_stage: VocabularyReviewStage | None = None
    next_review_at: datetime | None = None
    used_in_sentence: bool = False
    awaiting_sentence: bool = False
    sentence_attempts: int = 0


class VocabularyReviewResult(BaseModel):
    """Result of evaluating a user's vocabulary review answer."""

    model_config = ConfigDict(extra="forbid")

    vocabulary_id: str
    user_id: str
    french_word: str
    correct: bool
    shelved: bool
    finished: bool
    current_review_stage: VocabularyReviewStage | None
    next_review_at: datetime | None
    awaiting_sentence: bool = False


class ReferencedVocabularyReview(BaseModel):
    """A previously prompted vocabulary review resolved from a quoted bot message."""

    model_config = ConfigDict(extra="forbid")

    vocabulary_id: str
    user_id: str
    telegram_user_id: int
    french_word: str
    english_description: str


class VocabularySynonymHint(BaseModel):
    """Decision for giving the user a second chance on a synonym-style answer."""

    model_config = ConfigDict(extra="forbid")

    give_second_chance: bool
    distinction: str | None = None


class VocabularySentenceEvaluation(BaseModel):
    """Evaluation of a user's sentence using a vocabulary word."""

    model_config = ConfigDict(extra="forbid")

    acceptable: bool
    corrected_sentence: str | None = None
    feedback: str


class VocabularySentenceExamples(BaseModel):
    """Example sentences showing correct usage of a vocabulary word."""

    model_config = ConfigDict(extra="forbid")

    sentences: list[str] = Field(min_length=5, max_length=5)


class VocabularyDescriptionRefinement(BaseModel):
    """Refined vocabulary phrasing when the direct gloss is too close to the French word."""

    model_config = ConfigDict(extra="forbid")

    assistant_reply: str
    english_description: str
