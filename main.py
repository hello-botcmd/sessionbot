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
from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler

from config import BOT_TOKEN
from database.db import db
from handlers import start, manage, guard, my_accounts, admin

# ── Logging ───────────────────────────────────────────────────────────────
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

    # ── Register handlers (ORDER MATTERS!) ─────────────────────────────
    # ConversationHandlers go first so they capture their entry points
    # before any loose CallbackQueryHandler can steal them.
    start.register(application)       # /start command + back_main callback
    manage.register(application)      # Manage account ConversationHandler
    guard.register(application)       # Guard mode ConversationHandler
    my_accounts.register(application) # Paginated accounts + actions
    admin.register(application)       # /addsudo, /rmsudo, /help etc.

    # ── NO global wildcard fallback ────────────────────────────────────
    # A pattern="^.*$" fallback would intercept ALL unhandled callbacks,
    # breaking ConversationHandler state transitions. Let unmatched
    # callbacks silently expire instead.

    logger.info("🚀 Starting polling...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
