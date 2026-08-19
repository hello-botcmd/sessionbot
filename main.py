#!/usr/bin/env python3
"""
Telegram Session Manager Bot
─────────────────────────────
"""
import logging
import sys

from telegram.ext import ApplicationBuilder

from config import BOT_TOKEN, API_ID, API_HASH
from database.db import db
from handlers import start, manage, guard, my_accounts, admin
from utils.guard import GuardManager

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def post_init(application):
    await db.connect()
    logger.info("✅ MongoDB connected")
    logger.info("✅ Bot started")


async def post_stop(application):
    # Stop all running guard loops and disconnect their clients cleanly.
    manager = GuardManager(application)
    await manager.stop_all()
    await db.close()
    logger.info("🛑 MongoDB closed")


async def error_handler(update, context):
    """Log unhandled exceptions without killing the bot."""
    logger.error(
        "Exception while handling an update %s:", update, exc_info=context.error
    )


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set in .env!")
        sys.exit(1)
    if not API_ID or not API_HASH:
        logger.error("❌ API_ID and API_HASH must be set in .env (from my.telegram.org)!")
        sys.exit(1)

    logger.info("API_ID=%s  API_HASH=%s…", API_ID, API_HASH[:4] if API_HASH else "MISSING")
    if len(API_HASH) != 32:
        logger.warning(
            "⚠️ API_HASH looks wrong (length %d, expected 32 hex chars). "
            "Sessions will fail to connect if it is incorrect.", len(API_HASH)
        )

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .concurrent_updates(True)
        .build()
    )
    application.add_error_handler(error_handler)

    # Registration order matters:
    #  - ConversationHandlers first, so their ``back_main`` fallbacks get the
    #    callback while a conversation is active (and clean up their clients).
    #  - The global ``back_main`` handler (start.py) is registered LAST, so it
    #    only fires for non-conversation menus (My Accounts, Help).
    manage.register(application)
    guard.register(application)
    my_accounts.register(application)
    admin.register(application)
    start.register(application)

    logger.info("🚀 Starting polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
