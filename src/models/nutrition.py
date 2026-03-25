from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, ConfigDict, Field

from .common import MacroBreakdown


class IngredientEstimate(BaseModel):
    """Structured ingredient-level estimate for a nutrition analysis."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "Short ingredient or component name with at most 2 words, such as avocado, rice, olive oil, "
            "or cherry tomatoes."
        )
    )
    amount: str = Field(
        description=(
            "Estimated amount in a short user-friendly format such as 120 g, 250 ml, 1 avocado, 6 pieces, "
            "half avocado, or ~25 g. Keep it concise and prefer ~ instead of words like about or approximately."
        )
    )
    calories: float = Field(ge=0, description="Estimated calories for this ingredient amount.")


class NutritionAnalysis(BaseModel):
    """Structured nutrition result for a food or drink image."""

    model_config = ConfigDict(extra="forbid")

    ingredients: list[IngredientEstimate] = Field(
        description="Ingredient-by-ingredient breakdown. Put the ingredients in the order they appear in the dish."
    )
    category: str = Field(description="High-level classification, such as food, drink, or unknown.")
    calories: float = Field(
        ge=0,
        description="Estimated total calories for the pictured item. This should be the model's summed estimate."
    )
    item_count: int = Field(
        default=1,
        ge=1,
        description=(
            "How many copies of the pictured item this entry represents. Use 1 by default unless the application "
            "explicitly provides a larger multiplier."
        ),
    )
    macros: MacroBreakdown
    tags: list[str] = Field(description="Descriptive tags about the pictured item. Use an empty list when none apply.")
    alcohol_units: float = Field(
        ge=0,
        description="Estimated alcohol units, if any. Use 0.0 when there is no alcohol."
    )

    def to_dict(self) -> Dict[str, object]:
        return self.model_dump()


class NutritionCorrectionResult(BaseModel):
    """Decision and payload for correcting a prior nutrition analysis from follow-up text."""

    model_config = ConfigDict(extra="forbid")

    apply_correction: bool = Field(
        description="Whether the user's follow-up message should update the previous nutrition analysis."
    )
    analysis: NutritionAnalysis | None = Field(
        description=(
            "The revised nutrition analysis when apply_correction is true. Use null when the message is not a "
            "nutrition correction."
        )
    )
