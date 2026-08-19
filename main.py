#!/usr/bin/env python3
"""
Telegram Session Manager Bot
─────────────────────────────
"""

import logging
import sys
from telegram.ext import ApplicationBuilder

from config import BOT_TOKEN
from database.db import db
from handlers import start, manage, guard, my_accounts, admin

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
    await db.close()
    logger.info("🛑 MongoDB closed")


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set in .env!")
        sys.exit(1)

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .concurrent_updates(True)
        .build()
    )

    # Register handlers — ConversationHandlers first
    start.register(application)
    manage.register(application)
    guard.register(application)
    my_accounts.register(application)
    admin.register(application)

    # ⚠️ NO wildcard pattern="^.*$" fallback — it would steal callbacks
    # from active ConversationHandlers and break all state transitions.

    logger.info("🚀 Starting polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
