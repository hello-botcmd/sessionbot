import asyncio
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
from telethon.errors import FloodWaitError

from config import API_ID, API_HASH
from database.models import save_account, update_account, is_authorized
from keyboards.inline import guard_back_kb, cancel_kb, main_menu_kb
from utils.helpers import get_devices, terminate_device, format_account_info, check_spam_status, safe_edit
from utils.session_utils import verify_and_get_client
from utils.guard import GuardManager

logger = logging.getLogger(__name__)

WAITING_GUARD_HEX = 0


async def _notify(application, entry: dict, text: str):
    try:
        await application.bot.send_message(
            entry["chat_id"], text, parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Guard notify failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════════════════
async def guard_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info("🛡️ Guard button clicked by %s (callback=%s)",
                update.effective_user.id, query.data)

    if not await is_authorized(update.effective_user.id):
        await safe_edit(query, "⛔ **Access Denied.**\n\nYou are not authorized to use this bot.")
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

    status_msg = await update.message.reply_text("🔄 Activating Guard Mode...")

    client, info = await verify_and_get_client(hex_string, API_ID, API_HASH)
    if client is None:
        await status_msg.edit_text(f"❌ {info}", reply_markup=cancel_kb("guard"))
        return WAITING_GUARD_HEX

    try:
        me = await client.get_me()
        devices = await get_devices(client)
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

        # Terminate all non-current sessions immediately
        killed = 0
        for dev in devices:
            if not dev.get("current"):
                if await terminate_device(client, dev["hash"]):
                    killed += 1

        # Register the guard with the manager and start the background loop
        manager = GuardManager(context.application)
        key = manager.key(user_id, me.id)
        if manager.get(key):
            await manager.stop(key, notify=False)
        task = asyncio.create_task(_guard_loop(context.application, key))
        manager.add(user_id, me.id, client, update.effective_chat.id, task)

        await update_account(str(account_id), {
            "guard_active": True,
            "guard_allow_until": None,
        })

        info_text = format_account_info(info)
        info_text += (
            f"\n🛡️ **GUARD MODE ACTIVE**\n\n"
            f"├─ Sessions terminated: {killed}\n"
            f"├─ Spam: {spam_status}\n"
            f"├─ Checking every {manager.interval}s\n"
            f"└─ Status: ✅ **Monitoring**\n\n"
            f"_Any new login will be terminated and you'll be notified._"
        )

        await status_msg.edit_text(info_text, parse_mode="Markdown", reply_markup=guard_back_kb())
        return ConversationHandler.END

    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(f"❌ Error: {e}", reply_markup=cancel_kb("guard"))
        return WAITING_GUARD_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  BACKGROUND LOOP
# ═══════════════════════════════════════════════════════════════════════════
async def _guard_loop(application, key: str):
    manager = GuardManager(application)
    logger.info(f"Guard loop started: {key}")

    try:
        while True:
            entry = manager.get(key)
            if not entry or not entry["active"]:
                return

            client = entry["client"]

            # Reconnect / verify authorization
            try:
                if not client.is_connected():
                    await client.connect()
                if not await client.is_user_authorized():
                    await _notify(application, entry, "⚠️ **Guard stopped** — session expired.")
                    await manager.stop(key)
                    return
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Guard {key} reconnect error: {e}")
                await asyncio.sleep(3)
                continue

            # Fetch devices (respect flood waits)
            try:
                devices = await get_devices(client, raise_errors=True)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Guard {key} device error: {e}")
                await asyncio.sleep(3)
                continue

            allow_until = entry.get("allow_until")
            for dev in devices:
                if dev.get("current"):
                    continue
                # Honour a temporary "allow login" window
                if allow_until and datetime.now(timezone.utc) < allow_until:
                    continue
                if await terminate_device(client, dev["hash"]):
                    await _notify_terminated(application, entry, dev)

            await asyncio.sleep(manager.interval)

    except asyncio.CancelledError:
        logger.info(f"Guard loop cancelled: {key}")
    except Exception as e:
        logger.error(f"Guard loop error: {e}")


async def _notify_terminated(application, entry: dict, dev: dict):
    """Notify once per terminated device hash (dedupe)."""
    h = dev.get("hash")
    if h in entry["notified"]:
        return
    entry["notified"].add(h)

    text = (
        "🚨 **Unauthorized Login Terminated!** 🚨\n\n"
        "📱 Session killed immediately:\n"
        f"├─ Model: {dev.get('device_model', 'Unknown')}\n"
        f"├─ Platform: {dev.get('platform', 'Unknown')}\n"
        f"├─ IP: {dev.get('ip') or 'Unknown'}\n"
        f"├─ Region: {dev.get('region') or '?'} {dev.get('country') or ''}\n"
        f"├─ App: {dev.get('app_name', '')}\n"
        f"└─ Action: ✅ **Terminated**"
    )
    await _notify(application, entry, text)


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
            await safe_edit(query, "⛔ **Access Denied.**\n\nYou are not authorized to use this bot.")
        elif update.message:
            await update.message.reply_text("⛔ **Access Denied.**\n\nYou are not authorized to use this bot.")
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
            await safe_edit(query, "⛔ **Access Denied.**\n\nYou are not authorized to use this bot.")
        elif update.message:
            await update.message.reply_text("⛔ **Access Denied.**\n\nYou are not authorized to use this bot.")
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
