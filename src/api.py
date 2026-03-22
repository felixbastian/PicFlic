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
from .bot import create_telegram_application
from .models import ImageRecord, NutritionAnalysis
from .db import PostgresDatabase
from .logging_config import setup_logging
from .logging_context import bind_log_context, generate_process_id, reset_log_context

logger = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    image_path: str = Field(description="Path to the image file to analyze.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecordResponse(BaseModel):
    id: str
    image_path: str
    analysis: NutritionAnalysis
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


def _describe_update(update: Update) -> str:
    if update.message is None:
        return "non_message_update"
    if update.message.photo:
        return "photo_message"
    if update.message.text:
        return "text_message"
    return "message_other"


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
        _bot_application = create_telegram_application(agent, config.telegram_token, _db)
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
        context_token = bind_log_context(
            process_id=generate_process_id("telegram"),
            telegram_user_id=update.effective_user.id if update.effective_user else None,
            update_id=update.update_id,
            action="telegram_webhook",
        )
        try:
            logger.info(
                "Received Telegram webhook",
                extra={
                    "event": "telegram_webhook_received",
                    "update_kind": _describe_update(update),
                    "payload_keys": sorted(payload.keys()),
                },
            )

            if _db is not None and update.effective_user and update.message and update.message.photo:
                user_id = await _db.get_or_create_user(
                    telegram_user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    first_name=update.effective_user.first_name,
                    last_name=update.effective_user.last_name,
                )
                bind_log_context(user_id=user_id)
                pending_user_ids = _bot_application.bot_data.setdefault("_picflic_user_ids", {})
                pending_user_ids[update.update_id] = user_id
                logger.info(
                    "Preloaded warehouse user id for photo webhook",
                    extra={"event": "telegram_user_preloaded"},
                )

            await _bot_application.process_update(update)
            logger.info(
                "Processed Telegram webhook",
                extra={"event": "telegram_webhook_processed", "update_kind": _describe_update(update)},
            )
            return {"status": "ok"}
        finally:
            reset_log_context(context_token)
    except Exception as e:
        logger.exception("Failed to process webhook: %s", str(e), extra={"event": "telegram_webhook_failed"})
        raise HTTPException(status_code=500, detail=f"Error processing update: {str(e)}")


@app.get("/health")
def health() -> dict[str, str]:
    config = load_config()
    return {
        "status": "ok",
        "database_path": str(config.database_path),
        "postgres_enabled": str(config.postgres_enabled).lower(),
    }
