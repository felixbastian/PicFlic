from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict
import uuid

from pydantic import BaseModel, ConfigDict, Field


class MacroBreakdown(BaseModel):
    """Explicit macro-nutrient schema for structured output."""

    model_config = ConfigDict(extra="forbid")

    carbs: float = Field(ge=0, description="Estimated grams of carbohydrates.")
    protein: float = Field(ge=0, description="Estimated grams of protein.")
    fat: float = Field(ge=0, description="Estimated grams of fat.")


class ImageAnalysis(BaseModel):
    """A minimal analysis result for a photo."""

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


@dataclass
class ImageRecord:
    """Record stored in the database."""

    id: str
    image_path: str
    analysis: ImageAnalysis
    created_at: str

    @classmethod
    def from_analysis(cls, image_path: str, analysis: ImageAnalysis) -> "ImageRecord":
        return cls(
            id=str(uuid.uuid4()),
            image_path=image_path,
            analysis=analysis,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "image_path": self.image_path,
            "analysis": self.analysis.to_dict(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ImageRecord":
        analysis_data = data["analysis"]
        return cls(
            id=data["id"],
            image_path=data["image_path"],
            analysis=ImageAnalysis.model_validate(analysis_data),
            created_at=data["created_at"],
        )
