import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from config import OWNER_IDS
from database.models import (
    add_sudo_user,
    remove_sudo_user,
    get_all_sudo_users,
    is_sudo_user,
    save_mail,
    get_mail,
    remove_mail,
)
from keyboards.inline import admin_back_kb
from utils.helpers import verify_mail

logger = logging.getLogger(__name__)


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = is_owner(user_id)
    is_sudo = await is_sudo_user(user_id)

    text = "📚 **Help — Available Commands**\n\n"
    text += (
        "**👤 User Commands:**\n"
        "├─ `/start` — Start the bot & show main menu\n"
        "├─ `/help` — Show this help message\n"
        "├─ `/addmail email app_password` — Save & verify your login mail\n"
        "├─ `/checkmail` — Check that your saved mail works (verification msg)\n"
        "├─ `/mymail` — View saved mail\n"
        "└─ `/rmmail` — Remove saved mail\n\n"
    )

    if is_sudo or is_admin:
        text += (
            "**🔧 Sudo/Admin Commands:**\n"
            "├─ `/addsudo userid` — Add a sudo user (id or reply)\n"
            "├─ `/rmsudo userid` — Remove a sudo user\n"
            "├─ `/sudolist` — List all sudo users\n"
            "└─ All bot features are available to you\n\n"
        )

    if is_admin:
        text += "**👑 Owner Commands:**\n└─ Full access to all bot features\n\n"

    text += "**💡 Tip:** Use the buttons in the main menu for most features."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_back_kb())


async def add_sudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("❌ Only owners can use this command.")
        return

    target_id = None
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
            text += f"├─ `{su['user_id']}` (added by `{su.get('added_by', '?')}`)\n"
    else:
        text += "├─ _(none)_\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def add_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save + verify a login mail. Usage: /addmail email app_password"""
    user_id = update.effective_user.id

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Usage: `/addmail email@gmail.com your_app_password`\n\n"
            "_Generate an app password from your Google/Outlook/Yahoo account settings._",
            parse_mode="Markdown",
        )
        return

    email = context.args[0]
    app_password = " ".join(context.args[1:])

    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ That doesn't look like a valid email address.")
        return

    status = await update.message.reply_text("🧪 Verifying mail connection...")

    result = await verify_mail(email, app_password)
    if result.get("ok"):
        await save_mail(user_id, email, app_password, verified=True, check_message="OK")
        text = (
            "✅ **Mail saved & verified!**\n\n"
            f"├─ Email: `{email}`\n"
            f"├─ IMAP: {result.get('host')}\n"
            f"├─ Unread: {result.get('unread')}\n"
            f"└─ Telegram emails: {result.get('telegram_emails')}\n\n"
            "_You can now use the Change Mail feature in Manage Account._"
        )
    else:
        await save_mail(user_id, email, app_password, verified=False,
                        check_message=str(result.get("error")))
        text = (
            "⚠️ **Mail saved but verification FAILED.**\n\n"
            f"Email: `{email}`\n\n"
            f"Reason: {result.get('error')}\n\n"
            "_Double-check the app password and that IMAP is enabled._"
        )

    await status.edit_text(text, parse_mode="Markdown")


async def check_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mail checker: /checkmail"""
    user_id = update.effective_user.id
    mail = await get_mail(user_id)
    if not mail:
        await update.message.reply_text(
            "❌ No mail saved. Use `/addmail email app_password` first.",
            parse_mode="Markdown",
        )
        return

    email = mail["email"]
    app_password = mail["app_password"]

    status = await update.message.reply_text("🧪 Checking mail...")

    result = await verify_mail(email, app_password)
    if result.get("ok"):
        await save_mail(user_id, email, app_password, verified=True, check_message="OK")
        text = (
            "✅ **Mail Verified**\n\n"
            f"├─ Email: `{email}`\n"
            f"├─ IMAP: {result.get('host')}\n"
            f"├─ Unread: {result.get('unread')}\n"
            f"└─ Telegram emails: {result.get('telegram_emails')}\n"
        )
        if result.get("latest"):
            text += f"\nLatest: `{result['latest']}`"
    else:
        await save_mail(user_id, email, app_password, verified=False,
                        check_message=str(result.get("error")))
        text = (
            "❌ **Mail check failed**\n\n"
            f"Email: `{email}`\n\n"
            f"Reason: {result.get('error')}\n\n"
            "_Fix the credentials with `/addmail`._"
        )

    await status.edit_text(text, parse_mode="Markdown")


async def my_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mail = await get_mail(user_id)
    if mail:
        verified = "✅ verified" if mail.get("verified") else "⚠️ not verified"
        last = mail.get("check_message") or mail.get("last_checked") or "—"
        await update.message.reply_text(
            f"📧 **Saved Mail**\n\n"
            f"├─ Email: `{mail['email']}`\n"
            f"├─ Status: {verified}\n"
            f"├─ Last check: `{last}`\n"
            f"└─ App password: `{mail['app_password'][:4]}••••`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ No mail saved. Use `/addmail email app_password`", parse_mode="Markdown"
        )


async def remove_mail_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await remove_mail(user_id)
    await update.message.reply_text("✅ Saved mail removed.")


def register(application):
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("addsudo", add_sudo_cmd))
    application.add_handler(CommandHandler("rmsudo", remove_sudo_cmd))
    application.add_handler(CommandHandler("sudolist", sudo_list_cmd))
    application.add_handler(CommandHandler("addmail", add_mail_cmd))
    application.add_handler(CommandHandler("checkmail", check_mail_cmd))
    application.add_handler(CommandHandler("mymail", my_mail_cmd))
    application.add_handler(CommandHandler("rmmail", remove_mail_cmd))
