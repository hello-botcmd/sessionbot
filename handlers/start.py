import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from keyboards.inline import main_menu_kb

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "🤖 **Welcome to Session Manager Bot!**\n\n"
    "Your all-in-one Telegram session management tool.\n\n"
    "**✨ Features:**\n"
    "├─ 🔑 **Manage Account** — Connect via hex, view dashboard, manage devices, fetch OTP, clear data, change mail\n"
    "├─ 🛡️ **Safe / Guard** — Keep your account guarded, auto-logout intruders within 2s\n"
    "├─ 👤 **My Accounts** — View all stored accounts, fetch OTP, allow temporary logins\n"
    "└─ 🔐 **Admin Controls** — Multi-owner, sudo user management, email config\n\n"
    "Select an option below to get started 👇"
)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    first_name = user.first_name if user else "User"
    text = f"👋 Hello **{first_name}**!\n\n{WELCOME_TEXT}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_kb())


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Back to Menu' — return to main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())


def register(application):
    """Register handlers for this module."""
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_main$"))
