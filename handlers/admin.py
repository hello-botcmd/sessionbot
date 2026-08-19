import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import OWNER_IDS
from database.models import (
    add_sudo_user, remove_sudo_user, get_all_sudo_users,
    is_sudo_user, save_mail, get_mail, remove_mail,
)
from keyboards.inline import admin_back_kb, main_menu_kb

logger = logging.getLogger(__name__)


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help based on user role."""
    user_id = update.effective_user.id

    is_admin = is_owner(user_id)
    is_sudo = await is_sudo_user(user_id)

    text = "📚 **Help — Available Commands**\n\n"

    # Public commands
    text += (
        "**👤 User Commands:**\n"
        "├─ `/start` — Start the bot & show main menu\n"
        "├─ `/help` — Show this help message\n"
        "├─ `/addmail email app_password` — Save login mail for change-mail feature\n"
        "├─ `/rmmail` — Remove saved mail\n"
        "└─ `/mymail` — View saved mail\n\n"
    )

    if is_sudo or is_admin:
        text += (
            "**🔧 Sudo/Admin Commands:**\n"
            "├─ `/addsudo userid` — Add a sudo user (requires specific user_id or reply)\n"
            "├─ `/rmsudo userid` — Remove a sudo user\n"
            "├─ `/sudolist` — List all sudo users\n"
            "└─ Use the bot normally — all features are available\n\n"
        )

    if is_admin:
        text += (
            "**👑 Owner Commands:**\n"
            "├─ All sudo commands\n"
            "└─ Full access to all bot features\n\n"
        )

    text += "**💡 Tip:** Use the buttons in the main menu for most features."

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_back_kb())


async def add_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a sudo user. Usage: /addsudo <userid> or reply to a user."""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("❌ Only owners can use this command.")
        return

    target_id = None

    # Check if replying to a message
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID. Use: `/addsudo 123456789`", parse_mode="Markdown")
            return

    if not target_id:
        await update.message.reply_text("❌ Specify a user ID or reply to a user. Use: `/addsudo 123456789`", parse_mode="Markdown")
        return

    if target_id == user_id:
        await update.message.reply_text("❌ You're already an owner.")
        return

    success = await add_sudo_user(target_id, user_id)
    if success:
        await update.message.reply_text(f"✅ User `{target_id}` added as sudo user.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ User `{target_id}` is already a sudo user.", parse_mode="Markdown")


async def remove_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a sudo user. Usage: /rmsudo <userid>"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("❌ Only owners can use this command.")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: `/rmsudo 123456789`", parse_mode="Markdown")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    success = await remove_sudo_user(target_id)
    if success:
        await update.message.reply_text(f"✅ User `{target_id}` removed from sudo.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ User `{target_id}` was not a sudo user.", parse_mode="Markdown")


async def sudo_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all sudo users."""
    user_id = update.effective_user.id
    if not (is_owner(user_id) or await is_sudo_user(user_id)):
        await update.message.reply_text("❌ You don't have permission.")
        return

    sudo_users = await get_all_sudo_users()

    text = "**👑 Owners:**\n"
    for oid in OWNER_IDS:
        text += f"├─ `{oid}` (Owner)\n"

    text += "\n**🔧 Sudo Users:**\n"
    if sudo_users:
        for su in sudo_users:
            added_by = su.get("added_by", "?")
            text += f"├─ `{su['user_id']}` (added by `{added_by}`)\n"
    else:
        text += "├─ _(none)_\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def add_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save email + app password. Usage: /addmail email app_password"""
    user_id = update.effective_user.id

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/addmail email@gmail.com your_app_password`\n\n"
            "_Generate an app password from your Google Account settings._",
            parse_mode="Markdown",
        )
        return

    email = context.args[0]
    app_password = " ".join(context.args[1:])

    await save_mail(user_id, email, app_password)
    await update.message.reply_text(
        f"✅ **Mail saved!**\n\nEmail: `{email}`\n\n"
        f"You can now use the Change Mail feature in Manage Account.",
        parse_mode="Markdown",
    )


async def remove_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove saved mail."""
    user_id = update.effective_user.id
    await remove_mail(user_id)
    await update.message.reply_text("✅ Saved mail removed.")


async def my_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show saved mail."""
    user_id = update.effective_user.id
    mail = await get_mail(user_id)
    if mail:
        await update.message.reply_text(
            f"📧 **Saved Mail**\n\nEmail: `{mail['email']}`\nApp Password: `{mail['app_password'][:4]}...`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ No mail saved. Use `/addmail email app_password`", parse_mode="Markdown")


def register(application):
    """Register admin command handlers."""
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("addsudo", add_sudo_cmd))
    application.add_handler(CommandHandler("rmsudo", remove_sudo_cmd))
    application.add_handler(CommandHandler("sudolist", sudo_list_cmd))
    application.add_handler(CommandHandler("addmail", add_mail_cmd))
    application.add_handler(CommandHandler("rmmail", remove_mail_cmd))
    application.add_handler(CommandHandler("mymail", my_mail_cmd))
