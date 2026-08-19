import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from keyboards.inline import (
    manage_dashboard_kb, device_dashboard_kb, terminate_confirm_kb,
    otp_menu_kb, back_to_dashboard_kb, cancel_kb, main_menu_kb,
)
from utils.session_utils import verify_and_get_client  # <-- FIXED: was verify_and_get_info
from utils.helpers import (
    check_spam_status, get_devices, terminate_device,
    clear_all_data, fetch_otp, format_account_info, format_device,
)
from database.models import save_account, get_account_by_user_id, save_mail
from config import API_ID, API_HASH
import asyncio

logger = logging.getLogger(__name__)

WAITING_HEX, DASHBOARD, DEVICE_LIST, CONFIRM_TERMINATE, WAITING_MAIL = range(5)


async def manage_account_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 **Manage Account**\n\n"
        "Please send your Telegram **hex session string**.\n"
        "It will be verified and connected securely.\n\n"
        "_Example:_ `92dc84c8ec61d3df12cfb6f798b5fcaba08a01...`\n\n"
        "_The bot will automatically detect the correct datacenter._",
        parse_mode="Markdown",
        reply_markup=cancel_kb("manage"),
    )
    return WAITING_HEX


async def receive_hex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hex_string = update.message.text.strip()
    user_id = update.effective_user.id

    status_msg = await update.message.reply_text(
        "🔄 Processing your session...\n"
        "├─ Decoding hex...\n"
        "├─ Probing datacenters...\n"
        "└─ Verifying account...",
        parse_mode="Markdown",
    )

    # verify_and_get_client auto-probes DCs 5→4→3→2→1 for raw hex
    client, info = await verify_and_get_client(hex_string, API_ID, API_HASH)

    if client is None:
        await status_msg.edit_text(
            f"❌ **Verification Failed**\n\n{info}",
            parse_mode="Markdown",
            reply_markup=cancel_kb("manage"),
        )
        return WAITING_HEX

    try:
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

        account_key = f"client_{user_id}_{info['id']}"
        context.user_data[account_key] = client
        context.user_data["current_account_key"] = account_key

        dash_text = format_account_info(info)
        dash_text += f"├─ **Devices**  : {len(devices)} connected\n"
        dash_text += f"├─ **Spam**     : {spam_status}\n"
        dash_text += f"└─ **Status**   : ✅ **Verified & Connected**"

        await status_msg.edit_text(
            dash_text,
            parse_mode="Markdown",
            reply_markup=manage_dashboard_kb(),
        )
        return DASHBOARD

    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(
            f"❌ **Error**\n\nCould not complete setup: {e}",
            parse_mode="Markdown",
            reply_markup=cancel_kb("manage"),
        )
        return WAITING_HEX


async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "mng_devices":
        return await show_devices(update, context)
    elif data == "mng_clear_all":
        return await confirm_clear_all(update, context)
    elif data == "mng_fetch_otp":
        return await show_otp_menu(update, context)
    elif data == "mng_change_mail":
        return await ask_mail(update, context)
    elif data.startswith("mng_back_dash_"):
        return await _show_dashboard(update, context)
    # OTP read handled directly in DASHBOARD state (see get_manage_conversation_handler)
    elif data.startswith("otp_read_"):
        return await read_otp(update, context)
    return DASHBOARD


async def _get_client_from_context(context, user_id: int):
    key = context.user_data.get("current_account_key")
    if not key:
        return None
    return context.user_data.get(key)


async def _show_dashboard(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)

    if not client or not client.is_connected():
        await query.edit_message_text(
            "❌ Session expired. Please reconnect from the main menu.",
            reply_markup=main_menu_kb(),
        )
        return ConversationHandler.END

    me = await client.get_me()
    devices = await get_devices(client)
    spam_status = await check_spam_status(client)

    info = {
        "id": me.id,
        "phone": getattr(me, "phone", "Unknown"),
        "first_name": getattr(me, "first_name", ""),
        "last_name": getattr(me, "last_name", ""),
        "username": getattr(me, "username", ""),
        "dc_id": client.session.dc_id if hasattr(client.session, "dc_id") else 0,
    }

    dash_text = format_account_info(info)
    dash_text += f"├─ **Devices**  : {len(devices)} connected\n"
    dash_text += f"├─ **Spam**     : {spam_status}\n"
    dash_text += f"└─ **Status**   : ✅ Connected"

    await query.edit_message_text(dash_text, parse_mode="Markdown", reply_markup=manage_dashboard_kb())
    return DASHBOARD


# ── Device Dashboard ──────────────────────────────────────────────────────
async def show_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)

    if not client:
        await query.edit_message_text("❌ Session lost. Start again.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    devices = await get_devices(client)
    if not devices:
        await query.edit_message_text(
            "📱 **Devices**\n\nNo active sessions found.",
            parse_mode="Markdown",
            reply_markup=back_to_dashboard_kb("_"),
        )
        return DASHBOARD

    context.user_data["devices_list"] = devices

    text = "📱 **Device Dashboard**\n\n"
    for i, dev in enumerate(devices):
        text += format_device(dev, i) + "\n"

    me = await client.get_me()
    db_account = await get_account_by_user_id(user_id, me.id)
    acc_id_str = str(db_account["_id"]) if db_account else f"{user_id}_{me.id}"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=device_dashboard_kb(devices, acc_id_str),
    )
    return DEVICE_LIST


async def device_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    if data.startswith("term_dev_"):
        parts = data.split("_")
        acc_id = parts[2]
        dev_idx = int(parts[3])
        devices = context.user_data.get("devices_list", [])

        if dev_idx < len(devices):
            dev = devices[dev_idx]
            text = f"⚠️ **Terminate Device?**\n\n{format_device(dev, dev_idx)}\n\nAre you sure?"
            await query.edit_message_text(text, parse_mode="Markdown",
                                          reply_markup=terminate_confirm_kb(acc_id, dev_idx))
            return CONFIRM_TERMINATE

    elif data.startswith("term_yes_"):
        dev_idx = int(data.split("_")[3])
        devices = context.user_data.get("devices_list", [])
        if dev_idx < len(devices):
            dev_hash = devices[dev_idx]["hash"]
            success = await terminate_device(client, dev_hash)
            if success:
                await query.edit_message_text("✅ Device terminated!\n\nRefreshing...",
                                              reply_markup=cancel_kb("refresh"))
                await asyncio.sleep(1)
                return await show_devices(update, context)
            else:
                await query.edit_message_text("❌ Failed to terminate device.",
                                              reply_markup=cancel_kb("device"))
                return DEVICE_LIST

    elif data.startswith("term_no_"):
        return await show_devices(update, context)

    elif data.startswith("revoke_bot_"):
        devices = context.user_data.get("devices_list", [])
        success_count = fail_count = 0
        for dev in devices:
            if not dev.get("current"):
                if await terminate_device(client, dev["hash"]):
                    success_count += 1
                else:
                    fail_count += 1

        text = (f"🔌 **Bot Session Revocation**\n\n"
                f"├─ Terminated: {success_count}\n"
                f"└─ Failed: {fail_count}\n\n_Keeping only your current session._")
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=back_to_dashboard_kb("_"))
        await asyncio.sleep(1)
        return await show_devices(update, context)

    return DEVICE_LIST


# ── Clear All ─────────────────────────────────────────────────────────────
async def confirm_clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚠️ **⚠️ DESTRUCTIVE ACTION ⚠️**\n\n"
        "This will **permanently delete** all:\n"
        "├─ Contacts\n"
        "├─ Private chats (DMs)\n"
        "├─ Group chats\n"
        "├─ Channels\n\n"
        "_This cannot be undone._\n\n"
        "Type **YES** to confirm, or click Cancel.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("clear_all"),
    )
    return CONFIRM_TERMINATE


async def handle_clear_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.upper() != "YES":
        await update.message.reply_text("Cancelled.", reply_markup=manage_dashboard_kb())
        return DASHBOARD

    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)
    if not client:
        await update.message.reply_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    status_msg = await update.message.reply_text("🗑️ Clearing all data... This may take a while.")
    result = await clear_all_data(client)

    await status_msg.edit_text(
        "✅ **Clear Complete**\n\n"
        f"├─ Contacts deleted: {result['contacts']}\n"
        f"├─ Chats removed: {result['dialogs']}\n"
        f"└─ Errors: {result['errors']}",
        parse_mode="Markdown",
        reply_markup=manage_dashboard_kb(),
    )
    return DASHBOARD


# ── Fetch OTP ─────────────────────────────────────────────────────────────
async def show_otp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    me = await client.get_me()
    phone = getattr(me, "phone", "Unknown")
    name = f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip()

    db_account = await get_account_by_user_id(user_id, me.id)
    acc_id_str = str(db_account["_id"]) if db_account else f"{user_id}_{me.id}"

    text = (f"📨 **Fetch OTP**\n\n"
            f"Account: **{name}**\n"
            f"Phone: `{phone}`\n\n"
            f"Click below to read the latest OTP from Telegram messages.")
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=otp_menu_kb(acc_id_str))
    return DASHBOARD


async def read_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    await query.edit_message_text("🔍 Searching for OTP...")
    otp = await fetch_otp(client)

    if otp:
        me = await client.get_me()
        phone = getattr(me, "phone", "Unknown")
        name = f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip()
        text = (f"✅ **OTP Found!**\n\n"
                f"Account: **{name}**\n"
                f"Phone: `{phone}`\n\n"
                f"📨 **Code:** `{otp}`\n\n"
                f"_This code expires after a few minutes._")
    else:
        text = "❌ No OTP found in recent messages.\n\nMake sure a login code was sent."

    me = await client.get_me()
    db_account = await get_account_by_user_id(user_id, me.id)
    acc_id_str = str(db_account["_id"]) if db_account else f"{user_id}_{me.id}"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=otp_menu_kb(acc_id_str))
    return DASHBOARD


# ── Change Mail ───────────────────────────────────────────────────────────
async def ask_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📧 **Change Login Mail**\n\n"
        "Send in format:\n"
        "`email@gmail.com app_password`\n\n"
        "Or click Cancel.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("change_mail"),
    )
    return WAITING_MAIL


async def receive_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    client = await _get_client_from_context(context, user_id)

    if not client:
        await update.message.reply_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("❌ Invalid format. Send as: `email app_password`",
                                        parse_mode="Markdown", reply_markup=cancel_kb("change_mail"))
        return WAITING_MAIL

    email, app_pass = parts[0], parts[1]
    try:
        await save_mail(user_id, email, app_pass)
        await update.message.reply_text(
            f"✅ **Mail Configuration Saved!**\n\nEmail: `{email}`",
            parse_mode="Markdown", reply_markup=manage_dashboard_kb(),
        )
        return DASHBOARD
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}", reply_markup=cancel_kb("change_mail"))
        return WAITING_MAIL


# ── Cancel ────────────────────────────────────────────────────────────────
async def cancel_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id

    account_key = context.user_data.get("current_account_key")
    if account_key:
        client = context.user_data.pop(account_key, None)
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
    context.user_data.pop("current_account_key", None)
    context.user_data.pop("devices_list", None)

    from handlers.start import WELCOME_TEXT
    if query:
        await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    else:
        await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ── Conversation Handler ──────────────────────────────────────────────────
def get_manage_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(manage_account_entry, pattern="^manage_account$")],
        states={
            WAITING_HEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_hex),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_manage$"),
            ],
            DASHBOARD: [
                CallbackQueryHandler(dashboard_handler, pattern="^mng_"),
                CallbackQueryHandler(read_otp, pattern="^otp_read_"),  # <-- NOW INSIDE conversation
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            DEVICE_LIST: [
                CallbackQueryHandler(device_action_handler, pattern="^term_"),
                CallbackQueryHandler(device_action_handler, pattern="^revoke_bot_"),
                CallbackQueryHandler(dashboard_handler, pattern="^mng_back_dash_"),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            CONFIRM_TERMINATE: [
                CallbackQueryHandler(device_action_handler, pattern="^term_yes_"),
                CallbackQueryHandler(device_action_handler, pattern="^term_no_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_clear_all_confirm),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            WAITING_MAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_mail),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_change_mail$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_manage, pattern="^back_main$")],
        name="manage_account",
        persistent=False,
    )


def register(application):
    """Register manage conversation handler."""
    application.add_handler(get_manage_conversation_handler())
