from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .common import TrackingTaskType


class RoutingDecision(BaseModel):
    """Routing decision for the top-level orchestrator."""

    model_config = ConfigDict(extra="forbid")

    task_type: TrackingTaskType = Field(description="Which specialist should handle the image.")
