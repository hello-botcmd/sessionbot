#!/usr/bin/env python3
"""
Telegram Session Manager Bot
─────────────────────────────
A comprehensive Telegram session management tool with:
  - Manage Account (device dashboard, clear all, fetch OTP, change mail)
  - Safe / Guard mode (auto-terminates new logins within 2s)
  - My Accounts (paginated list, fetch OTP, revoke, allow login window)
  - Multi-owner / sudo admin system
  - MongoDB storage with Motor async driver
"""

import logging
import sys
from telegram.ext import ApplicationBuilder, CallbackQueryHandler

from config import BOT_TOKEN
from database.db import db
from handlers import start, manage, guard, my_accounts, admin

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def post_init(application):
    """Initialize database connection on startup."""
    await db.connect()
    logger.info("✅ MongoDB connected")
    logger.info("✅ Bot started — waiting for commands")


async def post_stop(application):
    """Clean up on shutdown."""
    await db.close()
    logger.info("🛑 MongoDB connection closed")


def main():
    """Build and run the bot."""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set in .env file!")
        sys.exit(1)

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .concurrent_updates(True)
        .build()
    )

    # ── Register all handlers ────────────────────────────────────────────
    start.register(application)
    manage.register(application)
    guard.register(application)
    my_accounts.register(application)
    admin.register(application)

    # ── Global fallback for unhandled callbacks ───────────────────────────
    async def fallback_callback(update, context):
        query = update.callback_query
        if query:
            await query.answer("This button is not available right now.")
    application.add_handler(CallbackQueryHandler(fallback_callback, pattern="^.*$"))

    logger.info("🚀 Starting polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
