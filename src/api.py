"""REST API for PictoAgent."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from telegram import Update

from . import create_default_agent, load_config
from .agent import PictoAgent
from .models import ImageAnalysis, ImageRecord
from .main import create_telegram_application
from .logging_config import setup_logging
from .db import PostgresDatabase

logger = logging.getLogger(__name__)


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
_db = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of the bot application."""
    # Initialize logging
    setup_logging()
    
    global _bot_application, _db
    # Startup
    config = load_config()
    agent = create_default_agent()
    
    if config.postgres_enabled:
        _db = PostgresDatabase.from_config(config)
        await _db.connect()
    else:
        _db = None
        logger.warning("PostgreSQL is not configured; webhook user persistence is disabled")
    
    if config.telegram_token:
        logger.info("Creating Telegram application")
        _bot_application = create_telegram_application(agent, config.telegram_token)
        logger.info("Initializing Telegram application")
        await _bot_application.initialize()
        logger.info("Telegram application initialized successfully")
    else:
        logger.warning("No telegram token found, bot will not be initialized")
    yield
    # Shutdown
    if _bot_application is not None:
        logger.info("Shutting down Telegram application")
        await _bot_application.stop()
        logger.info("Telegram application shut down")
    
    if _db is not None:
        await _db.disconnect()


app = FastAPI(title="PictoAgent API", version="0.1.0", lifespan=lifespan)


@app.post("/webhook/telegram")
async def telegram_webhook(payload: dict) -> dict[str, str]:
    """Receive Telegram updates via webhook."""
    try:
        if _bot_application is None:
            logger.error("Bot application not initialized")
            raise HTTPException(status_code=500, detail="Bot not initialized")
        
        update = Update.de_json(payload, _bot_application.bot)
        
        # Handle user creation/lookup when PostgreSQL is configured.
        if _db is not None and update.effective_user:
            user_id = await _db.get_or_create_user(
                telegram_user_id=update.effective_user.id,
                username=update.effective_user.username,
                first_name=update.effective_user.first_name,
                last_name=update.effective_user.last_name,
            )
            logger.info(f"User {update.effective_user.username} has user_id {user_id}")
        
        await _bot_application.process_update(update)
        logger.debug(f"Processed update from {update.effective_user.username if update.effective_user else 'unknown'}")
        
        return {"status": "ok"}
    except Exception as e:
        logger.exception(f"Failed to process webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing update: {str(e)}")


@app.get("/health")
def health() -> dict[str, str]:
    config = load_config()
    return {
        "status": "ok",
        "database_path": str(config.database_path),
        "postgres_enabled": str(config.postgres_enabled).lower(),
    }


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
