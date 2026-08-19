import logging
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, ConversationHandler
from keyboards.inline import accounts_pagination_kb, account_detail_kb, main_menu_kb
from database.models import get_accounts_by_owner, get_account_by_id, update_account, delete_account
from utils.session_utils import verify_and_get_client  # <-- FIXED import
from utils.helpers import (
    check_spam_status, get_devices, fetch_otp,
    format_account_info, terminate_device,
)
from config import API_ID, API_HASH

logger = logging.getLogger(__name__)

PAGE_VIEWING, ACCOUNT_DETAIL = range(2)


async def my_accounts_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show paginated list of stored accounts."""
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id
    page = 0

    context.user_data["accounts_page"] = page
    return await _show_accounts_page(update, context, user_id, page)


async def _show_accounts_page(update, context, user_id: int, page: int):
    """Render a page of accounts."""
    accounts = await get_accounts_by_owner(user_id)
    if not accounts:
        text = "👤 **My Accounts**\n\nYou have no stored accounts.\nUse **Manage Account** to add one."
        await _respond(update, text, main_menu_kb())
        return ConversationHandler.END

    total_pages = max(1, (len(accounts) + 4) // 5)  # ceil division
    if page >= total_pages:
        page = total_pages - 1

    context.user_data["accounts_page"] = page
    context.user_data["accounts_list"] = accounts

    start = page * 5
    end = min(start + 5, len(accounts))
    text = f"👤 **My Accounts** (Page {page + 1}/{total_pages})\n\n"
    for i in range(start, end):
        acc = accounts[i]
        text += f"├─ {i + 1}. **{acc.get('name', 'Unknown')}**\n"
        text += f"│   📞 `{acc.get('phone', 'Unknown')}`\n\n"

    text += f"Total: {len(accounts)} account(s)"

    await _respond(update, text, accounts_pagination_kb(accounts, page, total_pages))
    return PAGE_VIEWING


async def _respond(update, text, kb):
    """Helper to edit or send message."""
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def accounts_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination and refresh."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("acc_page_"):
        page = int(data.split("_")[2])
        return await _show_accounts_page(update, context, user_id, page)

    elif data == "acc_refresh":
        page = context.user_data.get("accounts_page", 0)
        return await _show_accounts_page(update, context, user_id, page)

    elif data.startswith("acc_view_"):
        account_id = data.split("_")[2]
        return await _show_account_detail(update, context, account_id)

    return PAGE_VIEWING


async def _show_account_detail(update, context, account_id: str):
    """Show detail for a specific account."""
    query = update.callback_query
    account = await get_account_by_id(account_id)
    if not account:
        await query.edit_message_text("❌ Account not found.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    context.user_data["detail_account_id"] = account_id

    # Create a temporary client to get live info
    client, info = await verify_and_get_client(
        account.get("session_string") or account.get("hex_key", ""),
        API_ID, API_HASH,
    )

    if client:
        try:
            me = await client.get_me()
            devices = await get_devices(client)
            spam_status = await check_spam_status(client)

            info_text = (
                f"👤 **Account Detail**\n\n"
                f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
                f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
                f"├─ **User ID** : `{me.id}`\n"
                f"├─ **Devices** : {len(devices)}\n"
                f"└─ **Spam**    : {spam_status}\n"
            )

            # Cache client for later actions
            context.user_data[f"detail_client_{account_id}"] = client
            await query.edit_message_text(info_text, parse_mode="Markdown",
                                          reply_markup=account_detail_kb(account_id))
        except Exception as e:
            if client.is_connected():
                await client.disconnect()
            info_text = (
                f"👤 **Account Detail**\n\n"
                f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
                f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
                f"├─ **User ID** : `{account.get('user_id', '?')}`\n\n"
                f"⚠️ Could not fetch live data: {e}"
            )
            await query.edit_message_text(info_text, parse_mode="Markdown",
                                          reply_markup=account_detail_kb(account_id))
    else:
        info_text = (
            f"👤 **Account Detail**\n\n"
            f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
            f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
            f"├─ **User ID** : `{account.get('user_id', '?')}`\n\n"
            f"⚠️ Session expired. Some features unavailable."
        )
        await query.edit_message_text(info_text, parse_mode="Markdown",
                                      reply_markup=account_detail_kb(account_id))

    return ACCOUNT_DETAIL


async def account_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle account detail actions: Fetch OTP, Revoke, Allow Login."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("acc_otp_"):
        account_id = data.split("_")[2]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await query.edit_message_text("❌ Session expired. Go back and try again.",
                                          reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        await query.edit_message_text("🔍 Fetching OTP...")
        otp = await fetch_otp(client)
        account = await get_account_by_id(account_id)

        if otp:
            text = (
                f"✅ **OTP Found!**\n\n"
                f"Account: **{account.get('name', 'Unknown')}**\n"
                f"Phone: `{account.get('phone', 'Unknown')}`\n\n"
                f"📨 **Code:** `{otp}`"
            )
        else:
            text = "❌ No OTP found in recent messages."

        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=account_detail_kb(account_id))
        return ACCOUNT_DETAIL

    elif data.startswith("acc_revoke_"):
        account_id = data.split("_")[2]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await query.edit_message_text("❌ Session expired.", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        await query.edit_message_text("🔌 Revoking bot connection...")
        try:
            await client.disconnect()
            await delete_account(account_id)
            context.user_data.pop(f"detail_client_{account_id}", None)
            await query.edit_message_text(
                "✅ **Bot connection revoked and account removed.**",
                reply_markup=main_menu_kb(),
            )
            return ConversationHandler.END
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {e}", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

    elif data.startswith("acc_allow_"):
        account_id = data.split("_")[2]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await query.edit_message_text("❌ Session expired.", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        # Allow login for 60 seconds — stop checking
        allow_until = datetime.now(timezone.utc) + timedelta(seconds=60)
        await update_account(account_id, {"guard_allow_until": allow_until, "guard_active": False})

        await query.edit_message_text(
            "🔓 **Login Allowed for 60 seconds**\n\n"
            "Anyone can log in to this account within the next 60 seconds.\n"
            "After that, guard mode will reactivate automatically.\n\n"
            "_You will be notified when guard mode is restored._",
            parse_mode="Markdown",
            reply_markup=account_detail_kb(account_id),
        )

        # Schedule guard reactivation notification
        async def _notify_guard_back():
            await asyncio.sleep(60)
            try:
                await update_account(account_id, {"guard_allow_until": None, "guard_active": True})
                bot = context.application.bot
                await bot.send_message(
                    chat_id=user_id,
                    text="🛡️ **Guard Mode Reactivated**\n\n"
                         "The 60-second login window has ended.\n"
                         "Guard mode is now active again — any new login will be terminated.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        asyncio.create_task(_notify_guard_back())
        return ACCOUNT_DETAIL

    return ACCOUNT_DETAIL


async def back_to_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to accounts list."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # Clean up any detail client
    account_id = context.user_data.get("detail_account_id")
    if account_id:
        client = context.user_data.pop(f"detail_client_{account_id}", None)
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass

    page = context.user_data.get("accounts_page", 0)
    return await _show_accounts_page(update, context, user_id, page)


def register(application):
    """Register handlers."""
    entry = CallbackQueryHandler(my_accounts_entry, pattern="^my_accounts$")
    nav = CallbackQueryHandler(accounts_navigation, pattern="^acc_")
    detail = CallbackQueryHandler(account_actions, pattern="^acc_otp_|^acc_revoke_|^acc_allow_")
    back = CallbackQueryHandler(back_to_accounts, pattern="^acc_back$")

    application.add_handler(entry)
    application.add_handler(nav)
    application.add_handler(detail)
    application.add_handler(back)
