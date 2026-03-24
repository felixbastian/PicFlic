from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict
import uuid

from .common import TrackingTaskType
from .expense import ExpenseAnalysis
from .nutrition import NutritionAnalysis
from .recipe import RecipeAnalysis

AnalysisPayload = NutritionAnalysis | ExpenseAnalysis | RecipeAnalysis


@dataclass
class ImageRecord:
    """Record stored in the local database."""

    id: str
    image_path: str
    task_type: TrackingTaskType
    analysis: AnalysisPayload
    created_at: str

    @classmethod
    def from_analysis(
        cls,
        image_path: str,
        task_type: TrackingTaskType,
        analysis: AnalysisPayload,
    ) -> "ImageRecord":
        return cls(
            id=str(uuid.uuid4()),
            image_path=image_path,
            task_type=task_type,
            analysis=analysis,
            created_at=datetime.utcnow().isoformat() + "Z",
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "image_path": self.image_path,
            "task_type": self.task_type,
            "analysis": self.analysis.to_dict(),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "ImageRecord":
        analysis_data = data["analysis"]
        task_type = data.get("task_type", "nutrition")
        if task_type == "expense":
            analysis = ExpenseAnalysis.model_validate(analysis_data)
        elif task_type == "recipe":
            analysis = RecipeAnalysis.model_validate(analysis_data)
        else:
            analysis = NutritionAnalysis.model_validate(analysis_data)
        return cls(
            id=data["id"],
            image_path=data["image_path"],
            task_type=task_type,
            analysis=analysis,
            created_at=data["created_at"],
        )
