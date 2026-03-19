"""Command-line entrypoint for PictoAgent."""

from __future__ import annotations

import argparse
import json
import logging

from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram import Update

from . import AppConfig, PictoAgent, create_default_agent, load_config
from .logging_config import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze images and store records in the persistent PictoAgent database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze an image and store the result.")
    analyze_parser.add_argument("image_path", help="Path to the image to analyze.")

    subparsers.add_parser("list", help="List stored records.")

    subparsers.add_parser("bot", help="Start the Telegram bot.")
    return parser


def handle_analyze(agent: PictoAgent, config: AppConfig, image_path: str) -> int:
    """Handle the analyze command."""
    try:
        result = agent.process_image(image_path)
        print(json.dumps(result, indent=2))
        print(f"Database: {config.database_path}")
        return 0
    except Exception as e:
        print(f"Error analyzing image: {e}")
        return 1


def handle_list(agent: PictoAgent, config: AppConfig) -> int:
    """Handle the list command."""
    try:
        records = agent.list_records()
        print(f"Database: {config.database_path}")
        print(f"Total records: {len(records)}")
        if records:
            print("\nRecords:")
            for record in records:
                print(f"  ID: {record.id}")
                print(f"  Image: {record.image_path}")
                print(f"  Category: {record.analysis.category}")
                print(f"  Calories: {record.analysis.calories}")
                print(f"  Created: {record.created_at}")
                print()
        return 0
    except Exception as e:
        print(f"Error listing records: {e}")
        return 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text("Hi! Send me a photo of your food and I'll analyze it!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: PictoAgent) -> None:
    """Handle incoming messages."""
    try:
        user = update.effective_user.username if update.effective_user else "unknown"
        
        if update.message.photo:
            logger.info(f"Processing photo from {user}")
            # Handle photo
            photo = update.message.photo[-1]  # Get the largest photo
            file = await photo.get_file()
            
            # Download the photo
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                await file.download_to_drive(tmp_file.name)
                image_path = tmp_file.name
            
            try:
                result = agent.process_image(image_path)
                analysis = result['analysis']
                response = f"Category: {analysis['category']}\nCalories: {analysis['calories']}\nTags: {', '.join(analysis.get('tags', []))}"
                await update.message.reply_text(response)
                logger.info(f"Successfully analyzed photo from {user}")
            except Exception as e:
                logger.error(f"Failed to analyze image from {user}: {str(e)}")
                await update.message.reply_text(f"Error analyzing image: {e}")
            finally:
                os.unlink(image_path)
        else:
            logger.debug(f"Echoing text message from {user}")
            # Echo text messages
            await update.message.reply_text(update.message.text)
    except Exception as e:
        logger.exception(f"Error handling message: {str(e)}")
        try:
            await update.message.reply_text("Sorry, an error occurred while processing your message.")
        except:
            pass  # Don't fail if we can't send error message


def create_telegram_application(agent: PictoAgent, token: str) -> Application:
    """Create and configure the Telegram bot application."""
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO,
        lambda update, context: handle_message(update, context, agent)
    ))
    
    return application


def handle_bot(agent: PictoAgent, config: AppConfig) -> int:
    """Handle the bot command (polling mode for local testing)."""
    if not config.telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return 1
    
    try:
        application = create_telegram_application(agent, config.telegram_token)
        logger.info("Bot is running... Press Ctrl+C to stop.")
        application.run_polling()
        return 0
    except Exception as e:
        logger.exception(f"Error running bot: {e}")
        return 1


def main() -> int:
    setup_logging()
    
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_config()
        agent = create_default_agent()
    except Exception as e:
        logger.exception("Error initializing")
        return 1

    if args.command == "analyze":
        return handle_analyze(agent, config, args.image_path)
    elif args.command == "list":
        return handle_list(agent, config)
    elif args.command == "bot":
        return handle_bot(agent, config)
    else:
        logger.error(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
