"""REST API for PictoAgent."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import create_default_agent, load_config
from .agent import PictoAgent
from .models import ImageAnalysis, ImageRecord


class AnalyzeRequest(BaseModel):
    image_path: str = Field(description="Path to the image file to analyze.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecordResponse(BaseModel):
    id: str
    image_path: str
    analysis: ImageAnalysis
    created_at: str


def _record_to_response(record: ImageRecord) -> RecordResponse:
    return RecordResponse(
        id=record.id,
        image_path=record.image_path,
        analysis=record.analysis,
        created_at=record.created_at,
    )


def get_agent() -> PictoAgent:
    return create_default_agent()


app = FastAPI(title="PictoAgent API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    config = load_config()
    return {"status": "ok", "database_path": str(config.database_path)}


@app.post("/records/analyze", response_model=RecordResponse)
def analyze_record(payload: AnalyzeRequest, agent: PictoAgent = Depends(get_agent)) -> RecordResponse:
    result = agent.process_image(payload.image_path, metadata=payload.metadata)
    record = agent.get_record(result["record_id"])
    if record is None:
        raise HTTPException(status_code=500, detail="Record was not persisted.")

    return _record_to_response(record)


@app.get("/records", response_model=list[RecordResponse])
def list_records(agent: PictoAgent = Depends(get_agent)) -> list[RecordResponse]:
    return [_record_to_response(record) for record in agent.list_records()]


@app.get("/records/{record_id}", response_model=RecordResponse)
def get_record(record_id: str, agent: PictoAgent = Depends(get_agent)) -> RecordResponse:
    record = agent.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found.")

    return _record_to_response(record)
