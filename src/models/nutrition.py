from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, ConfigDict, Field

from .common import MacroBreakdown


class NutritionAnalysis(BaseModel):
    """Structured nutrition result for a food or drink image."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(description="High-level classification, such as food, drink, or unknown.")
    calories: float = Field(ge=0, description="Estimated calories for the pictured item.")
    macros: MacroBreakdown
    tags: list[str] = Field(description="Descriptive tags about the pictured item. Use an empty list when none apply.")
    alcohol_units: float = Field(
        ge=0,
        description="Estimated alcohol units, if any. Use 0.0 when there is no alcohol."
    )

    def to_dict(self) -> Dict[str, object]:
        return self.model_dump()
