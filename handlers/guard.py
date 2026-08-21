import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

from config import API_ID, API_HASH
from database.models import save_account, update_account, is_authorized
from keyboards.inline import guard_back_kb, cancel_kb, main_menu_kb
from utils.helpers import (
    format_account_info,
    check_spam_status,
    safe_edit,
    denied_text,
)
from utils.session_utils import verify_and_get_client
from utils.guard import GuardManager, start_guard

logger = logging.getLogger(__name__)

WAITING_GUARD_HEX = 0


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════════════════
async def guard_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info("🛡️ Guard button clicked by %s (callback=%s)",
                update.effective_user.id, query.data)

    if not await is_authorized(update.effective_user.id):
        await safe_edit(query, denied_text(update.effective_user.id))
        return ConversationHandler.END

    await safe_edit(query, 
        "🛡️ **Safe / Guard Account**\n\n"
        "Guard mode **monitors** your account and:\n"
        "├─ Keeps your session alive\n"
        "├─ **Auto-terminates** any new login\n"
        "└─ Notifies you of every attempt\n\n"
        "Send your **session string** to activate.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("guard"),
    )
    return WAITING_GUARD_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  ACTIVATE
# ═══════════════════════════════════════════════════════════════════════════
async def receive_guard_hex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hex_string = update.message.text.strip()
    user_id = update.effective_user.id

    status_msg = await update.message.reply_text("🛡️ Connecting to hex auth key...")

    client, info = await verify_and_get_client(hex_string, API_ID, API_HASH)
    if client is None:
        await status_msg.edit_text(f"❌ {info}", reply_markup=cancel_kb("guard"))
        return WAITING_GUARD_HEX

    try:
        me = await client.get_me()
        spam_status = await check_spam_status(client)

        name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
        account_id = await save_account(
            owner_id=user_id,
            hex_key=hex_string,
            phone=info.get("phone", "Unknown"),
            name=name or "Unknown",
            user_id=info.get("id", 0),
            dc_id=info.get("dc_id", 0),
            session_string=info.get("session_string", ""),
        )

        # Start the background guard loop (kept in bot_data). Existing sessions
        # are left untouched; only NEW logins after this point are terminated.
        manager = GuardManager(context.application)
        await start_guard(context.application, user_id, me.id,
                          client, update.effective_chat.id)

        await update_account(str(account_id), {
            "guard_active": True,
            "guard_allow_until": None,
        })

        info_text = format_account_info(info)
        info_text += (
            f"\n🛡️ **GUARD MODE ACTIVE**\n\n"
            f"├─ Spam: {spam_status}\n"
            f"├─ Checking every {manager.interval}s\n"
            f"└─ Status: ✅ **Monitoring**\n\n"
            f"_Existing sessions are kept. Any NEW login after this point will be terminated and you'll be notified._"
        )

        await status_msg.edit_text(info_text, parse_mode="Markdown", reply_markup=guard_back_kb())
        return ConversationHandler.END

    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(f"❌ Error: {e}", reply_markup=cancel_kb("guard"))
        return WAITING_GUARD_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  STATUS / DEACTIVATE (global handlers — the guard conversation has ended)
# ═══════════════════════════════════════════════════════════════════════════
async def guard_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        if query:
            await safe_edit(query, denied_text(update.effective_user.id))
        elif update.message:
            await update.message.reply_text(denied_text(update.effective_user.id))
        return

    manager = GuardManager(context.application)
    entries = manager.list_for_user(user_id)

    if not entries:
        text = "ℹ️ **No active guard.**\n\nUse **Safe / Guard** from the menu to start one."
        kb = main_menu_kb()
    else:
        lines = []
        for e in entries:
            phone = "?"
            try:
                me = await e["client"].get_me()
                phone = getattr(me, "phone", "?")
            except Exception:
                pass
            lines.append(
                f"✅ UID `{e['account_uid']}` · `{phone}` · every {manager.interval}s"
            )
            allow = e.get("allow_until")
            if allow and datetime.now(timezone.utc) < allow:
                lines.append(f"   └─ 🔓 login allowed until {allow.strftime('%H:%M:%S')}")
        text = "🛡️ **Active Guards**\n\n" + "\n".join(lines)
        kb = guard_back_kb()

    if query:
        await safe_edit(query, text, parse_mode="Markdown", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def guard_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    if not await is_authorized(user_id):
        if query:
            await safe_edit(query, denied_text(update.effective_user.id))
        elif update.message:
            await update.message.reply_text(denied_text(update.effective_user.id))
        return

    manager = GuardManager(context.application)
    stopped = await manager.stop_for_user(user_id)

    msg = (
        f"🛡️ **Guard mode deactivated.**\n\nStopped {stopped} account guard(s)."
        if stopped
        else "ℹ️ No active guard found."
    )

    if query:
        await safe_edit(query, msg, parse_mode="Markdown", reply_markup=main_menu_kb())
    elif update.message:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_menu_kb())


async def cancel_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    from handlers.start import WELCOME_TEXT
    if query:
        await safe_edit(query, WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    elif update.message:
        await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════
def get_guard_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(guard_entry, pattern=r"^guard_account$")],
        states={
            WAITING_GUARD_HEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_guard_hex),
                CallbackQueryHandler(cancel_guard, pattern=r"^cancel_guard$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_guard, pattern=r"^back_main$"),
            CommandHandler("cancel", cancel_guard),
        ],
        name="guard_account",
        persistent=False,
    )


def register(application):
    application.add_handler(get_guard_conversation_handler())
    # Global handlers (conversation has ended once a guard is running)
    application.add_handler(CallbackQueryHandler(guard_deactivate, pattern=r"^guard_deactivate$"))
    application.add_handler(CallbackQueryHandler(guard_status, pattern=r"^guard_status$"))
    application.add_handler(CommandHandler("guardstatus", guard_status))
    application.add_handler(CommandHandler("stopguard", guard_deactivate))
