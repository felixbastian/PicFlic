"""REST API for PictoAgent."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from telegram import Update

from . import create_default_agent, load_config
from .agent import PictoAgent
from .models import ImageAnalysis, ImageRecord
from .main import create_telegram_application


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


# Initialize bot application on startup
_bot_application = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of the bot application."""
    global _bot_application
    # Startup
    config = load_config()
    agent = create_default_agent()
    if config.telegram_token:
        _bot_application = create_telegram_application(agent, config.telegram_token)
    yield
    # Shutdown
    if _bot_application is not None:
        await _bot_application.stop()


app = FastAPI(title="PictoAgent API", version="0.1.0", lifespan=lifespan)


@app.post("/webhook/telegram")
async def telegram_webhook(payload: dict) -> dict[str, str]:
    """Receive Telegram updates via webhook."""
    try:
        print(f"DEBUG: Received webhook payload: {payload}")
        
        if _bot_application is None:
            print("ERROR: Bot application not initialized")
            raise HTTPException(status_code=500, detail="Bot not initialized")
        
        print("DEBUG: Processing update...")
        update = Update.de_json(payload, _bot_application.bot)
        print(f"DEBUG: Update processed: {update}")
        
        await _bot_application.process_update(update)
        print("DEBUG: Update processed successfully")
        
        return {"status": "ok"}
    except Exception as e:
        print(f"ERROR: Failed to process webhook: {str(e)}")
        print(f"ERROR: Payload was: {payload}")
        import traceback
        print(f"ERROR: Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error processing update: {str(e)}")


@app.get("/health")
def health() -> dict[str, str]:
    config = load_config()
    return {"status": "ok", "database_path": str(config.database_path)}


# @app.post("/records/analyze", response_model=RecordResponse)
# def analyze_record(payload: AnalyzeRequest, agent: PictoAgent = Depends(get_agent)) -> RecordResponse:
#     result = agent.process_image(payload.image_path, metadata=payload.metadata)
#     record = agent.get_record(result["record_id"])
#     if record is None:
#         raise HTTPException(status_code=500, detail="Record was not persisted.")

#     return _record_to_response(record)


# @app.get("/records", response_model=list[RecordResponse])
# def list_records(agent: PictoAgent = Depends(get_agent)) -> list[RecordResponse]:
#     return [_record_to_response(record) for record in agent.list_records()]


# @app.get("/records/{record_id}", response_model=RecordResponse)
# def get_record(record_id: str, agent: PictoAgent = Depends(get_agent)) -> RecordResponse:
#     record = agent.get_record(record_id)
#     if record is None:
#         raise HTTPException(status_code=404, detail="Record not found.")

#     return _record_to_response(record)
