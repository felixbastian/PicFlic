from __future__ import annotations

from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field


CarbSource = Literal["noodles", "rice", "potato", "bread"]
MeatType = Literal["chicken", "beef", "porc", "fish"]
FrequencyRotation = Literal["bi-weekly", "monthly", "occasionally", "seasonally"]


class RecipeAnalysis(BaseModel):
    """Structured output for a saved recipe or dish idea."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A short dish name.")
    description: str = Field(description="A short summary of the recipe or dish.")
    carb_source: CarbSource | None = Field(
        description="The primary carbohydrate source when present, otherwise null."
    )
    vegetarian: bool | None = Field(
        description="Whether the dish is vegetarian. Use null if this is unclear."
    )
    meat: MeatType | None = Field(
        description="The primary meat used in the dish when present, otherwise null."
    )
    frequency_rotation: FrequencyRotation | None = Field(
        description="How often the dish should rotate into meals, otherwise null."
    )

    def to_dict(self) -> Dict[str, object]:
        return self.model_dump()


class RecipeCollectionResult(RecipeAnalysis):
    """Structured result for adding a recipe from a text message."""

    workflow_type: Literal["recipe_collection"] = Field(description="Identifies the recipe collection workflow.")
