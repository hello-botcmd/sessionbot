import logging
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from keyboards.inline import guard_kb, guard_back_kb, cancel_kb, main_menu_kb
from utils.session_utils import verify_and_get_info
from utils.helpers import get_devices, check_spam_status, format_account_info, terminate_device
from database.models import save_account, get_accounts_by_owner, update_account
from config import API_ID, API_HASH

logger = logging.getLogger(__name__)

WAITING_GUARD_HEX, GUARD_ACTIVE = range(2)


async def guard_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry for Safe/Guard mode."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛡️ **Safe / Guard Account**\n\n"
        "This mode monitors your account and:\n"
        "├─ Keeps your session active\n"
        "├️ **Auto-terminates** any new login within **2 seconds**\n"
        "├─ Notifies you immediately about any unauthorized access\n\n"
        "Send your **hex session string** to activate guard mode.\n\n"
        "_Or click Cancel to go back._",
        parse_mode="Markdown",
        reply_markup=cancel_kb("guard"),
    )
    return WAITING_GUARD_HEX


async def receive_guard_hex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive hex and activate guard mode."""
    hex_string = update.message.text.strip()
    user_id = update.effective_user.id

    status_msg = await update.message.reply_text(
        "🔄 Activating Guard Mode...",
        parse_mode="Markdown",
    )

    client, info = await verify_and_get_info(hex_string, API_ID, API_HASH)
    if client is None:
        await status_msg.edit_text(
            f"❌ **Verification Failed**\n\n{info}",
            parse_mode="Markdown",
            reply_markup=cancel_kb("guard"),
        )
        return WAITING_GUARD_HEX

    try:
        me = await client.get_me()
        devices = await get_devices(client)
        spam_status = await check_spam_status(client)

        # Save account
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

        # Store client for guard loop
        guard_key = f"guard_client_{user_id}_{me.id}"
        context.application.bot_data[guard_key] = client
        context.application.bot_data[f"guard_active_{user_id}_{me.id}"] = True
        context.user_data["guard_user_id"] = me.id

        # Kill all non-current sessions immediately
        killed = 0
        for dev in devices:
            if not dev.get("current"):
                if await terminate_device(client, dev["hash"]):
                    killed += 1

        info_text = format_account_info(info)
        info_text += (
            f"\n🛡️ **GUARD MODE ACTIVE**\n\n"
            f"├─ Sessions terminated: {killed}\n"
            f"├─ Devices: {len(devices)}\n"
            f"├─ Spam: {spam_status}\n"
            f"├─ Check interval: Every 2 seconds\n"
            f"└─ Status: ✅ **Monitoring**\n\n"
            f"_Any new login will be automatically terminated within 2s._\n"
            f"_You will be notified of any unauthorized access._"
        )

        await status_msg.edit_text(info_text, parse_mode="Markdown",
                                   reply_markup=guard_back_kb(str(me.id)))

        # Start background guard task
        asyncio.create_task(_guard_loop(context, user_id, me.id, client))
        return GUARD_ACTIVE

    except Exception as e:
        if client and client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(
            f"❌ Error: {e}",
            reply_markup=cancel_kb("guard"),
        )
        return WAITING_GUARD_HEX


async def _guard_loop(context, user_id: int, account_user_id: int, client):
    """Background loop: check every 2s for new logins and terminate them."""
    guard_key = f"guard_active_{user_id}_{account_user_id}"
    try:
        while context.application.bot_data.get(guard_key, False):
            try:
                if not client.is_connected():
                    await client.connect()
                    if not await client.is_user_authorized():
                        break

                devices = await get_devices(client)
                for dev in devices:
                    if not dev.get("current"):
                        # Found a non-current session — terminate it
                        await terminate_device(client, dev["hash"])

                        # Notify user
                        try:
                            bot = context.application.bot
                            device_info = (
                                f"🚨 **Unauthorized Login Detected!** 🚨\n\n"
                                f"📱 New session was terminated immediately:\n"
                                f"├─ Model: {dev.get('device_model', 'Unknown')}\n"
                                f"├─ Platform: {dev.get('platform', 'Unknown')}\n"
                                f"├─ IP: {dev.get('ip', '')}\n"
                                f"├─ Region: {dev.get('region', '')}\n"
                                f"├─ App: {dev.get('app_name', '')} {dev.get('app_version', '')}\n"
                                f"└─ Action: ✅ **Terminated within 2s**"
                            )
                            await bot.send_message(
                                chat_id=user_id,
                                text=device_info,
                                parse_mode="Markdown",
                            )
                        except Exception:
                            pass

            except Exception as e:
                logger.warning(f"Guard loop error: {e}")

            await asyncio.sleep(2)  # Check every 2 seconds
    except asyncio.CancelledError:
        pass
    finally:
        context.application.bot_data[guard_key] = False
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass


async def guard_deactivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deactivate guard and return to main menu."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    guard_uid = context.user_data.get("guard_user_id")
    if guard_uid:
        guard_key = f"guard_active_{user_id}_{guard_uid}"
        context.application.bot_data[guard_key] = False
        client_key = f"guard_client_{user_id}_{guard_uid}"
        client = context.application.bot_data.pop(client_key, None)
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass

    await query.edit_message_text(
        "🛡️ **Guard mode deactivated.**\n\nAll monitoring stopped.",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )
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


def get_guard_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(guard_entry, pattern="^guard_account$")],
        states={
            WAITING_GUARD_HEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_guard_hex),
                CallbackQueryHandler(cancel_guard, pattern="^cancel_guard$"),
            ],
            GUARD_ACTIVE: [
                CallbackQueryHandler(guard_deactivate, pattern="^guard_account$"),
                CallbackQueryHandler(cancel_guard, pattern="^back_main$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_guard, pattern="^back_main$")],
        name="guard_account",
        persistent=False,
    )


def register(application):
    application.add_handler(get_guard_conversation_handler())
