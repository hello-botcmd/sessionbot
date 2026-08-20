import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from keyboards.inline import main_menu_kb
from utils.helpers import safe_edit

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "🤖 **Welcome to Session Manager Bot!**\n\n"
    "Your all-in-one Telegram session management tool.\n\n"
    "**✨ Features:**\n"
    "├─ 🔑 **Manage Account** — connect, device dashboard, terminate devices, "
    "clear all, fetch OTP, change mail\n"
    "├─ 🛡️ **Safe / Guard** — auto-logout intruders and notify you\n"
    "├─ 👤 **My Accounts** — view stored accounts, fetch OTP, allow temporary logins\n"
    "└─ 🔐 **Admin** — sudo users, email config, mail checker\n\n"
    "Select an option below 👇"
)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    first_name = user.first_name if user else "User"
    text = f"👋 Hello **{first_name}**!\n\n{WELCOME_TEXT}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global fallback: return to the main menu.

    NOTE: registered LAST in main.py. Active ConversationHandlers (manage /
    guard) are registered first, so their own ``back_main`` fallbacks get the
    callback while a conversation is running (and clean up their clients). This
    handler only fires for non-conversation menus (My Accounts, Help).
    """
    query = update.callback_query
    await query.answer()
    await safe_edit(query, WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())


def register(application):
    """Register handlers for this module (must be called AFTER the conversations)."""
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern=r"^back_main$"))
