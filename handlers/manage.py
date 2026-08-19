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
from database.models import save_account, get_account_by_user_id, save_mail, get_mail
from keyboards.inline import (
    manage_dashboard_kb,
    device_dashboard_kb,
    terminate_confirm_kb,
    clear_all_confirm_kb,
    otp_menu_kb,
    back_to_dashboard_kb,
    cancel_kb,
    main_menu_kb,
    change_mail_kb,
)
from utils.helpers import (
    check_spam_status,
    get_devices,
    terminate_device,
    clear_all_data,
    fetch_otp,
    read_email_otp,
    format_account_info,
    format_device,
)
from utils.session_utils import verify_and_get_client

logger = logging.getLogger(__name__)

# Conversation states
WAITING_HEX, DASHBOARD, DEVICE_LIST, CONFIRM_TERMINATE, CONFIRM_CLEAR, \
    WAITING_CHANGE_MAIL_EMAIL, WAITING_CHANGE_MAIL_CONFIRM = range(7)


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY — ask for hex
# ═══════════════════════════════════════════════════════════════════════════

async def manage_account_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 **Manage Account**\n\n"
        "Please send your Telegram **hex session string**.\n"
        "The bot will auto-detect the correct datacenter.\n\n"
        "_Example:_ `92dc84c8ec61d3df12cfb6f798b5fcab...`",
        parse_mode="Markdown",
        reply_markup=cancel_kb("manage"),
    )
    return WAITING_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  RECEIVE HEX — verify, probe DCs, show dashboard
# ═══════════════════════════════════════════════════════════════════════════

async def receive_hex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hex_string = update.message.text.strip()
    user_id = update.effective_user.id

    status_msg = await update.message.reply_text(
        "🔄 Processing...\n├─ Decoding\n├─ Probing DCs (5→4→3→2→1)\n└─ Verifying",
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
        await save_account(
            owner_id=user_id,
            hex_key=hex_string,
            phone=info.get("phone", "Unknown"),
            name=name or "Unknown",
            user_id=info.get("id", 0),
            dc_id=info.get("dc_id", 0),
            session_string=info.get("session_string", ""),
        )

        # Store client in user_data
        context.user_data["current_client"] = client
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
        if client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(
            f"❌ **Error**\n\n{e}",
            parse_mode="Markdown",
            reply_markup=cancel_kb("manage"),
        )
        return WAITING_HEX


# ═══════════════════════════════════════════════════════════════════════════
#  DASHBOARD — route button clicks
# ═══════════════════════════════════════════════════════════════════════════

async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "mng_devices":
        return await show_devices(update, context)
    elif data == "mng_clear_all":
        return await ask_clear_all(update, context)
    elif data == "mng_fetch_otp":
        return await show_otp_menu(update, context)
    elif data == "mng_change_mail":
        return await ask_change_mail_email(update, context)
    elif data == "mng_back_dash":
        return await _refresh_dashboard(update, context)
    elif data == "otp_read":
        return await read_otp(update, context)
    return DASHBOARD


async def _get_client(context, user_id: int = None):
    """Get stored Telethon client."""
    return context.user_data.get("current_client")


async def _refresh_dashboard(update, context):
    """Re-show dashboard with live data."""
    query = update.callback_query
    client = await _get_client(context)

    if not client or not client.is_connected():
        await query.edit_message_text(
            "❌ Session expired. Reconnect from main menu.",
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

    text = format_account_info(info)
    text += f"├─ **Devices**  : {len(devices)} connected\n"
    text += f"├─ **Spam**     : {spam_status}\n"
    text += f"└─ **Status**   : ✅ Connected"

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=manage_dashboard_kb())
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  DEVICE DASHBOARD — list devices with terminate buttons
# ═══════════════════════════════════════════════════════════════════════════

async def show_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    client = await _get_client(context)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    devices = await get_devices(client)
    if not devices:
        await query.edit_message_text(
            "📱 **Devices**\n\nNo active sessions found.",
            parse_mode="Markdown",
            reply_markup=back_to_dashboard_kb(),
        )
        return DASHBOARD

    # Store devices in user_data for termination lookups
    context.user_data["device_list"] = devices

    text = "📱 **Device Dashboard**\n\n"
    for i, dev in enumerate(devices):
        text += format_device(dev, i) + "\n"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=device_dashboard_kb(len(devices)),
    )
    return DEVICE_LIST


async def device_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    client = await _get_client(context)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    devices = context.user_data.get("device_list", [])

    # ── Terminate specific device: show confirmation ──────────────
    if data.startswith("term|"):
        idx = int(data.split("|")[1])
        if idx < len(devices):
            dev = devices[idx]
            text = f"⚠️ **Terminate This Device?**\n\n{format_device(dev, idx)}\n_This will log them out._"
            await query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=terminate_confirm_kb(idx),
            )
            return CONFIRM_TERMINATE

    # ── Confirm termination ──────────────────────────────────────
    elif data.startswith("term_yes|"):
        idx = int(data.split("|")[1])
        if idx < len(devices):
            dev_hash = devices[idx]["hash"]
            ok = await terminate_device(client, dev_hash)
            if ok:
                await query.edit_message_text(
                    "✅ **Device terminated!**\n\nRefreshing device list...",
                    reply_markup=cancel_kb("refresh"),
                )
                await asyncio.sleep(1)
                return await show_devices(update, context)
            else:
                await query.edit_message_text(
                    "❌ Failed to terminate device.",
                    reply_markup=cancel_kb("device"),
                )
                return DEVICE_LIST

    # ── Cancel termination ───────────────────────────────────────
    elif data == "term_no":
        return await show_devices(update, context)

    # ── Revoke ALL bot sessions ──────────────────────────────────
    elif data == "revoke_bot":
        ok_count = fail_count = 0
        for dev in devices:
            if not dev.get("current"):
                if await terminate_device(client, dev["hash"]):
                    ok_count += 1
                else:
                    fail_count += 1

        text = (
            f"🔌 **Bot Session Revocation**\n\n"
            f"├─ Terminated: {ok_count}\n"
            f"└─ Failed: {fail_count}\n\n"
            "_Your current session is preserved._"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_to_dashboard_kb())
        await asyncio.sleep(1)
        return await show_devices(update, context)

    return DEVICE_LIST


# ═══════════════════════════════════════════════════════════════════════════
#  CLEAR ALL — inline buttons, no text input required
# ═══════════════════════════════════════════════════════════════════════════

async def ask_clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚠️ **⚠️ DESTRUCTIVE ACTION ⚠️**\n\n"
        "This will **permanently erase**:\n"
        "├─ All contacts\n"
        "├─ All private chats (DMs)\n"
        "├─ All group chats\n"
        "├─ All channels\n\n"
        "_This is irreversible._",
        parse_mode="Markdown",
        reply_markup=clear_all_confirm_kb(),
    )
    return CONFIRM_CLEAR


async def handle_clear_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "clr_yes":
        client = await _get_client(context)
        if not client:
            await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
            return ConversationHandler.END

        await query.edit_message_text("🗑️ Clearing all data... This may take a minute.")
        result = await clear_all_data(client)

        await query.edit_message_text(
            "✅ **Clear Complete**\n\n"
            f"├─ Contacts deleted: {result['contacts']}\n"
            f"├─ Chats removed: {result['dialogs']}\n"
            f"└─ Errors: {result['errors']}",
            parse_mode="Markdown",
            reply_markup=manage_dashboard_kb(),
        )
        return DASHBOARD

    elif data == "clr_no":
        # Back to dashboard
        return await _refresh_dashboard(update, context)

    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  FETCH OTP
# ═══════════════════════════════════════════════════════════════════════════

async def show_otp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client = await _get_client(context)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    me = await client.get_me()
    phone = getattr(me, "phone", "Unknown")
    name = f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip()

    text = (
        f"📨 **Fetch OTP**\n\n"
        f"Account: **{name}**\n"
        f"Phone: `{phone}`\n\n"
        f"Click the button to read the latest OTP."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=otp_menu_kb())
    return DASHBOARD


async def read_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    client = await _get_client(context)

    if not client:
        await query.edit_message_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    await query.edit_message_text("🔍 Searching for OTP...")
    otp = await fetch_otp(client)

    if otp:
        me = await client.get_me()
        phone = getattr(me, "phone", "Unknown")
        name = f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip()
        text = (
            f"✅ **OTP Found!**\n\n"
            f"Account: **{name}**\n"
            f"Phone: `{phone}`\n\n"
            f"📨 **Code:** `{otp}`\n\n"
            f"_Tap again to re-fetch._"
        )
    else:
        text = "❌ No OTP found in recent messages.\n\nMake sure a login code was sent to this account."

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=otp_menu_kb())
    return DASHBOARD


# ═══════════════════════════════════════════════════════════════════════════
#  CHANGE MAIL — full email verification flow
# ═══════════════════════════════════════════════════════════════════════════

async def ask_change_mail_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for the target email + app password."""
    query = update.callback_query
    await query.answer()

    # Check if user has saved mail via /addmail
    user_id = update.effective_user.id
    saved_mail = await get_mail(user_id)

    text = (
        "📧 **Change Telegram Login Mail**\n\n"
        "This will set a **recovery email** for 2FA on this account.\n\n"
        "Telegram will send a verification code to the email.\n"
        "The bot reads it automatically via IMAP.\n\n"
        "Send in this format:\n"
        "`email@gmail.com your_app_password`\n\n"
        "_You need a Gmail app password (not your regular password)._\n"
        "_If you've saved mail via /addmail, just send:_ `USE_SAVED`"
    )

    if saved_mail:
        text += f"\n\n📧 Saved: `{saved_mail['email']}`"

    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=cancel_kb("change_mail"),
    )
    return WAITING_CHANGE_MAIL_EMAIL


async def receive_change_mail_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive email + app password, start the verification flow."""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    client = await _get_client(context)

    if not client:
        await update.message.reply_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    # Check for USE_SAVED shortcut
    if text.upper() == "USE_SAVED":
        saved = await get_mail(user_id)
        if not saved:
            await update.message.reply_text(
                "❌ No saved mail found. Use `/addmail email app_password` first, "
                "or send the email and app password directly.",
                parse_mode="Markdown",
                reply_markup=cancel_kb("change_mail"),
            )
            return WAITING_CHANGE_MAIL_EMAIL
        email_address = saved["email"]
        app_password = saved["app_password"]
    else:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text(
                "❌ Invalid format. Send as:\n`email@gmail.com app_password`\n\n"
                "Or use `USE_SAVED` if you saved mail via /addmail.",
                parse_mode="Markdown",
                reply_markup=cancel_kb("change_mail"),
            )
            return WAITING_CHANGE_MAIL_EMAIL
        email_address, app_password = parts[0], parts[1]

    status_msg = await update.message.reply_text(
        "📧 Sending verification code to email...\n"
        "This may take a moment.",
        parse_mode="Markdown",
    )

    try:
        # Save the mail first
        await save_mail(user_id, email_address, app_password)

        # Use Telethon's edit_2fa to set the recovery email
        # The email_code_callback reads the code from IMAP
        async def email_code_callback(code_length: int) -> str:
            await status_msg.edit_text(
                f"📧 Code sent to `{email_address}`\n"
                f"Reading via IMAP...",
                parse_mode="Markdown",
            )
            code = await read_email_otp(email_address, app_password, wait_seconds=20)
            if code:
                return code
            # If auto-read fails, ask user to provide code manually
            raise ValueError("IMAP_NEED_MANUAL")

        try:
            await client.edit_2fa(
                email=email_address,
                email_code_callback=email_code_callback,
            )

            await status_msg.edit_text(
                f"✅ **Email Verified & Set!**\n\n"
                f"Recovery email: `{email_address}`\n\n"
                f"_This email can be used to recover your account._",
                parse_mode="Markdown",
                reply_markup=manage_dashboard_kb(),
            )
            return DASHBOARD

        except ValueError as exc:
            if str(exc) == "IMAP_NEED_MANUAL":
                # Couldn't auto-read — ask user for the code
                context.user_data["pending_email"] = email_address
                context.user_data["pending_app_pass"] = app_password
                await status_msg.edit_text(
                    "⚠️ Could not auto-read the code from email.\n\n"
                    "Please check `{email_address}` and send the "
                    "verification code you received.",
                    parse_mode="Markdown",
                    reply_markup=cancel_kb("change_mail_manual"),
                )
                return WAITING_CHANGE_MAIL_CONFIRM
            raise

    except Exception as e:
        await status_msg.edit_text(
            f"❌ Failed to change mail: {e}",
            parse_mode="Markdown",
            reply_markup=cancel_kb("change_mail"),
        )
        return WAITING_CHANGE_MAIL_EMAIL


async def receive_manual_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive manual verification code from user."""
    code_text = update.message.text.strip()
    client = await _get_client(context)

    if not client:
        await update.message.reply_text("❌ Session lost.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    email = context.user_data.get("pending_email")

    try:
        from telethon import functions, types

        # Try to verify the email with the code the user provided
        verification = types.EmailVerificationCode(code=code_text)
        await client(functions.account.VerifyEmailRequest(
            purpose=types.EmailVerifyPurposeLoginSetup(
                phone_number=context.user_data.get("current_phone", ""),
                phone_code_hash="",
            ),
            verification=verification,
        ))

        await update.message.reply_text(
            f"✅ **Email Verified & Set!**\n\nEmail: `{email}`",
            parse_mode="Markdown",
            reply_markup=manage_dashboard_kb(),
        )
        return DASHBOARD

    except Exception as e:
        await update.message.reply_text(
            f"❌ Verification failed: {e}\n\nTry again with the correct code, or /cancel.",
            parse_mode="Markdown",
            reply_markup=cancel_kb("change_mail_manual"),
        )
        return WAITING_CHANGE_MAIL_CONFIRM


# ═══════════════════════════════════════════════════════════════════════════
#  CANCEL — clean up and return to main menu
# ═══════════════════════════════════════════════════════════════════════════

async def cancel_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id

    # Disconnect client
    client = context.user_data.pop("current_client", None)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass

    # Clear all user_data keys we set
    for key in ("current_client", "current_user_id", "current_phone",
                "current_name", "device_list", "pending_email",
                "pending_app_pass"):
        context.user_data.pop(key, None)

    from handlers.start import WELCOME_TEXT
    if query:
        await query.edit_message_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    else:
        await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
#  CONVERSATION HANDLER BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def get_manage_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(manage_account_entry, pattern="^manage_account$")],
        states={
            WAITING_HEX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_hex),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_manage$"),
            ],
            DASHBOARD: [
                CallbackQueryHandler(dashboard_handler, pattern="^mng_|^otp_read$"),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            DEVICE_LIST: [
                CallbackQueryHandler(device_action_handler, pattern="^term"),
                CallbackQueryHandler(device_action_handler, pattern="^revoke_bot$"),
                CallbackQueryHandler(dashboard_handler, pattern="^mng_back_dash$"),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            CONFIRM_TERMINATE: [
                CallbackQueryHandler(device_action_handler, pattern="^term_yes|"),
                CallbackQueryHandler(device_action_handler, pattern="^term_no$"),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            CONFIRM_CLEAR: [
                CallbackQueryHandler(handle_clear_all_confirm, pattern="^clr_"),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_"),
            ],
            WAITING_CHANGE_MAIL_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_change_mail_email),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_change_mail$"),
            ],
            WAITING_CHANGE_MAIL_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_code),
                CallbackQueryHandler(cancel_manage, pattern="^cancel_change_mail_manual$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_manage, pattern="^back_main$")],
        name="manage_account",
        persistent=False,
    )


def register(application):
    """Register manage conversation handler."""
    application.add_handler(get_manage_conversation_handler())
