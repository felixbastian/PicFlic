from __future__ import annotations

from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field

EXPENSE_CATEGORIES = (
    "Lebensmitteleinkäufe",
    "Kleidung",
    "Dm / Rossmann",
    "Mobilität",
    "Mensa",
    "Bäcker",
    "Taxi / Einzelfahrkarten",
    "Entertainment",
    "Ausgehen (Restaurant / Bar / Kino etc.)",
    "Sonstige",
    "Geschenke",
    "Reisen",
)

ExpenseCategory = Literal[
    "Lebensmitteleinkäufe",
    "Kleidung",
    "Dm / Rossmann",
    "Mobilität",
    "Mensa",
    "Bäcker",
    "Taxi / Einzelfahrkarten",
    "Entertainment",
    "Ausgehen (Restaurant / Bar / Kino etc.)",
    "Sonstige",
    "Geschenke",
    "Reisen",
]


class ExpenseAnalysis(BaseModel):
    """Structured output for an expense receipt."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(description="A short description of the expense.")
    expense_total_amount_in_euros: float = Field(ge=0, description="The total receipt amount in euros.")
    category: ExpenseCategory = Field(description="One of the configured expense categories.")

    def to_dict(self) -> Dict[str, object]:
        return self.model_dump()
