"""REST API for PictoAgent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from telegram import Update

from . import create_default_agent, create_default_vocabulary_agent, load_config
from .agents import MainAgent
from .bot import create_telegram_application
from .models import ImageRecord, NutritionAnalysis
from .db import PostgresDatabase
from .logging_config import setup_logging
from .logging_context import bind_log_context, generate_process_id, reset_log_context
from .vocab_conversation_bot import create_vocabulary_conversation_telegram_application
from .vocab_bot import (
    VocabularyConversationTrainer,
    create_vocabulary_telegram_application,
    dispatch_due_vocabulary_reviews,
)

logger = logging.getLogger(__name__)

_MAIN_BOT_WEBHOOK_PATH = "/webhook/telegram"
_VOCAB_BOT_WEBHOOK_PATH = "/webhook/telegram/vocabulary"
_VOCAB_CONVERSATION_BOT_WEBHOOK_PATH = "/webhook/telegram/vocabulary-conversation"


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


def get_agent() -> MainAgent:
    return create_default_agent()


# Initialize bot application on startup
_main_bot_application = None
_vocab_bot_application = None
_vocab_conversation_bot_application = None
_db = None
_vocab_conversation_trainer = None


def _describe_update(update: Update) -> str:
    if update.message is None:
        return "non_message_update"
    if update.message.photo:
        return "photo_message"
    if update.message.text:
        return "text_message"
    return "message_other"


def _extract_update_debug_fields(update: Update) -> dict[str, Any]:
    message = update.message
    text = message.text if message is not None else None
    entities = message.entities if message is not None else None
    command = None
    if entities and text:
        for entity in entities:
            if entity.type == "bot_command" and entity.offset == 0:
                command = text[: entity.length]
                break

    return {
        "chat_id": message.chat_id if message is not None else None,
        "message_id": message.message_id if message is not None else None,
        "has_text": bool(text),
        "text_preview": text[:80] if text else None,
        "command": command,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of the bot application."""
    # Initialize logging
    setup_logging()
    
    global _main_bot_application, _vocab_bot_application, _vocab_conversation_bot_application, _db
    global _vocab_conversation_trainer
    # Startup
    config = load_config()
    main_agent = create_default_agent()
    vocabulary_agent = create_default_vocabulary_agent()
    _vocab_conversation_trainer = VocabularyConversationTrainer()
    
    if config.postgres_enabled:
        _db = PostgresDatabase.from_config(config)
        await _db.connect()
    else:
        _db = None
        logger.warning("PostgreSQL is not configured; webhook user persistence is disabled")
    
    if config.telegram_token:
        logger.info("Creating Telegram application")
        _main_bot_application = create_telegram_application(main_agent, config.telegram_token, _db)
        logger.info("Initializing Telegram application")
        await _main_bot_application.initialize()
        logger.info(
            "Telegram application initialized successfully",
            extra={
                "event": "main_telegram_application_initialized",
                "bot_username": "PictoAgent",
                "expected_webhook_path": _MAIN_BOT_WEBHOOK_PATH,
            },
        )
    else:
        logger.warning("No telegram token found, bot will not be initialized")
    if config.vocab_telegram_token:
        logger.info("Creating vocabulary Telegram application")
        _vocab_bot_application = create_vocabulary_telegram_application(
            vocabulary_agent,
            config.vocab_telegram_token,
            _db,
        )
        logger.info("Initializing vocabulary Telegram application")
        await _vocab_bot_application.initialize()
        logger.info(
            "Vocabulary Telegram application initialized successfully",
            extra={
                "event": "vocabulary_telegram_application_initialized",
                "bot_username": config.vocab_bot_username,
                "expected_webhook_path": _VOCAB_BOT_WEBHOOK_PATH,
            },
        )
    else:
        logger.warning("No vocabulary telegram token found, vocabulary bot will not be initialized")
    if config.vocab_conversation_telegram_token:
        logger.info("Creating vocabulary conversation Telegram application")
        _vocab_conversation_bot_application = create_vocabulary_conversation_telegram_application(
            _vocab_conversation_trainer,
            config.vocab_conversation_telegram_token,
            _db,
        )
        logger.info("Initializing vocabulary conversation Telegram application")
        await _vocab_conversation_bot_application.initialize()
        logger.info(
            "Vocabulary conversation Telegram application initialized successfully",
            extra={
                "event": "vocabulary_conversation_telegram_application_initialized",
                "bot_username": config.vocab_conversation_bot_username,
                "expected_webhook_path": _VOCAB_CONVERSATION_BOT_WEBHOOK_PATH,
            },
        )
    else:
        logger.warning(
            "No vocabulary conversation telegram token found, vocabulary conversation bot will not be initialized"
        )
    yield
    # Shutdown
    if _main_bot_application is not None:
        logger.info("Shutting down Telegram application")
        await _main_bot_application.stop()
        logger.info("Telegram application shut down")
    if _vocab_bot_application is not None:
        logger.info("Shutting down vocabulary Telegram application")
        await _vocab_bot_application.stop()
        logger.info("Vocabulary Telegram application shut down")
    if _vocab_conversation_bot_application is not None:
        logger.info("Shutting down vocabulary conversation Telegram application")
        await _vocab_conversation_bot_application.stop()
        logger.info("Vocabulary conversation Telegram application shut down")
    
    if _db is not None:
        await _db.disconnect()


app = FastAPI(title="PictoAgent API", version="0.1.0", lifespan=lifespan)


@app.post("/webhook/telegram")
async def telegram_webhook(payload: dict) -> dict[str, str]:
    """Receive Telegram updates via webhook."""
    return await _process_telegram_webhook(
        payload,
        _main_bot_application,
        process_prefix="telegram",
        action="telegram_webhook",
        preload_main_photo_user=True,
    )


@app.post("/webhook/telegram/vocabulary")
async def vocabulary_telegram_webhook(payload: dict) -> dict[str, str]:
    """Receive Telegram updates for the separate vocabulary bot."""
    return await _process_telegram_webhook(
        payload,
        _vocab_bot_application,
        process_prefix="vocabulary-telegram",
        action="vocabulary_telegram_webhook",
        preload_main_photo_user=False,
    )


@app.post("/webhook/telegram/vocabulary-conversation")
async def vocabulary_conversation_telegram_webhook(payload: dict) -> dict[str, str]:
    """Receive Telegram updates for the dedicated vocabulary conversation bot."""
    return await _process_telegram_webhook(
        payload,
        _vocab_conversation_bot_application,
        process_prefix="vocabulary-conversation-telegram",
        action="vocabulary_conversation_telegram_webhook",
        preload_main_photo_user=False,
    )


async def _process_telegram_webhook(
    payload: dict,
    application,
    *,
    process_prefix: str,
    action: str,
    preload_main_photo_user: bool,
) -> dict[str, str]:
    """Receive Telegram updates via webhook."""
    try:
        if application is None:
            logger.error("Bot application not initialized")
            raise HTTPException(status_code=500, detail="Bot not initialized")

        update = Update.de_json(payload, application.bot)
        context_token = bind_log_context(
            process_id=generate_process_id(process_prefix),
            telegram_user_id=update.effective_user.id if update.effective_user else None,
            update_id=update.update_id,
            action=action,
        )
        try:
            logger.info(
                "Received Telegram webhook",
                extra={
                    "event": "telegram_webhook_received",
                    "update_kind": _describe_update(update),
                    "bot_route": action,
                    "payload_keys": sorted(payload.keys()),
                    **_extract_update_debug_fields(update),
                },
            )

            if preload_main_photo_user and _db is not None and update.effective_user and update.message and update.message.photo:
                user_id = await _db.get_or_create_user(
                    telegram_user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    first_name=update.effective_user.first_name,
                    last_name=update.effective_user.last_name,
                )
                bind_log_context(user_id=user_id)
                pending_user_ids = application.bot_data.setdefault("_picflic_user_ids", {})
                pending_user_ids[update.update_id] = user_id
                logger.info(
                    "Preloaded warehouse user id for photo webhook",
                    extra={"event": "telegram_user_preloaded"},
                )

            await application.process_update(update)
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


@app.post("/jobs/vocabulary-reviews/run")
async def run_vocabulary_reviews(
    x_job_secret: str | None = Header(default=None, alias="X-Job-Secret"),
) -> dict[str, int | str]:
    """Dispatch due vocabulary review prompts to Telegram."""
    config = load_config()
    if not config.review_job_secret or x_job_secret != config.review_job_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    if _vocab_bot_application is None or _db is None:
        raise HTTPException(status_code=500, detail="Vocabulary bot or database not initialized")

    context_token = bind_log_context(
        process_id=generate_process_id("vocabulary-review-job"),
        action="vocabulary_review_job",
        workflow="vocabulary_review",
    )
    try:
        sent_count = await dispatch_due_vocabulary_reviews(_vocab_bot_application, _db)
        logger.info(
            "Dispatched due vocabulary reviews",
            extra={"event": "vocabulary_review_job_completed", "sent_count": sent_count},
        )
        return {"status": "ok", "sent_count": sent_count}
    finally:
        reset_log_context(context_token)


@app.post("/jobs/vocabulary-conversations/run")
async def run_vocabulary_conversations(
    x_job_secret: str | None = Header(default=None, alias="X-Job-Secret"),
) -> dict[str, int | str]:
    """Start new daily vocabulary conversations for eligible users."""
    config = load_config()
    if not config.review_job_secret or x_job_secret != config.review_job_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    if _vocab_conversation_bot_application is None or _db is None or _vocab_conversation_trainer is None:
        raise HTTPException(
            status_code=500,
            detail="Vocabulary conversation bot, trainer, or database not initialized",
        )

    context_token = bind_log_context(
        process_id=generate_process_id("vocabulary-conversation-job"),
        action="vocabulary_conversation_job",
        workflow="vocabulary_conversation",
    )
    try:
        started_count = await _vocab_conversation_trainer.dispatch_daily_conversations(
            _vocab_conversation_bot_application,
            _db,
        )
        logger.info(
            "Started daily vocabulary conversations",
            extra={"event": "vocabulary_conversation_job_completed", "started_count": started_count},
        )
        return {"status": "ok", "started_count": started_count}
    finally:
        reset_log_context(context_token)


@app.get("/health")
def health() -> dict[str, str]:
    config = load_config()
    return {
        "status": "ok",
        "database_path": str(config.database_path),
        "postgres_enabled": str(config.postgres_enabled).lower(),
        "vocab_bot_enabled": str(bool(config.vocab_telegram_token)).lower(),
        "vocab_conversation_bot_enabled": str(bool(config.vocab_conversation_telegram_token)).lower(),
    }
