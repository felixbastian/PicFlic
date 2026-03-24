from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MacroBreakdown(BaseModel):
    """Explicit macro-nutrient schema for structured output."""

    model_config = ConfigDict(extra="forbid")

    carbs: float = Field(ge=0, description="Estimated grams of carbohydrates.")
    protein: float = Field(ge=0, description="Estimated grams of protein.")
    fat: float = Field(ge=0, description="Estimated grams of fat.")

TrackingTaskType = Literal["nutrition", "expense", "recipe"]
