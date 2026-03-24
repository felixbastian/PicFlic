from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TextWorkflowType = Literal["echo", "expense_query", "nutrition_query", "vocabulary", "recipe_collection"]


class TextRoutingDecision(BaseModel):
    """Routing decision for a text message."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: TextWorkflowType = Field(description="Which text workflow should handle the message.")


class SQLQueryPlan(BaseModel):
    """Read-only SQL plan generated from a natural-language question."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: Literal["expense_query", "nutrition_query"] = Field(
        description="Which domain-specific query workflow should execute the SQL."
    )
    explanation: str = Field(description="Short explanation of what the query is looking for.")
    sql_query: str = Field(description="A single safe read-only PostgreSQL SELECT query using $1 for user_id.")
    response_template: str = Field(
        description=(
            "A short natural-language answer for single-row responses that uses only the placeholders "
            "{result_value}, {result_unit}, {result_label}, {period_label}. "
            "Multi-row responses are formatted by the application."
        )
    )
