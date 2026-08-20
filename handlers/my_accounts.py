import logging
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, ConversationHandler

from config import API_ID, API_HASH, ALLOW_LOGIN_SECONDS
from database.models import (
    get_accounts_by_owner,
    get_account_by_id,
    update_account,
    delete_account,
    set_last_otp,
    is_authorized,
)
from keyboards.inline import accounts_pagination_kb, account_detail_kb, main_menu_kb
from utils.helpers import check_spam_status, get_devices, fetch_otp, safe_edit
from utils.session_utils import verify_and_get_client
from utils.guard import GuardManager

logger = logging.getLogger(__name__)

PAGE_VIEWING, ACCOUNT_DETAIL = range(2)


async def _disconnect_detail(context, account_id):
    client = context.user_data.pop(f"detail_client_{account_id}", None)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass


async def my_accounts_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id

    if not await is_authorized(user_id):
        if query:
            await safe_edit(query, "⛔ **Access Denied.**\n\nYou are not authorized to use this bot.")
        return ConversationHandler.END

    context.user_data["accounts_page"] = 0
    return await _show_accounts_page(update, context, user_id, 0)


async def _show_accounts_page(update, context, user_id: int, page: int):
    accounts = await get_accounts_by_owner(user_id)
    if not accounts:
        await _respond(update, "👤 **My Accounts**\n\nNo accounts stored yet.\nUse **Manage Account** to add one.", main_menu_kb())
        return ConversationHandler.END

    total_pages = max(1, (len(accounts) + 4) // 5)
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    context.user_data["accounts_page"] = page

    start = page * 5
    end = min(start + 5, len(accounts))
    text = f"👤 **My Accounts** (Page {page + 1}/{total_pages})\n\n"
    for i in range(start, end):
        acc = accounts[i]
        text += f"├─ {i + 1}. **{acc.get('name', 'Unknown')}**  `{acc.get('phone', 'Unknown')}`\n"

    text += f"\nTotal: {len(accounts)} account(s)"

    await _respond(update, text, accounts_pagination_kb(accounts, page, total_pages))
    return PAGE_VIEWING


async def _respond(update, text, kb):
    if update.callback_query:
        await safe_edit(update.callback_query, text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def accounts_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("acc_page:"):
        page = int(data.split(":", 1)[1])
        return await _show_accounts_page(update, context, user_id, page)
    if data == "acc_refresh":
        page = context.user_data.get("accounts_page", 0)
        return await _show_accounts_page(update, context, user_id, page)
    if data.startswith("acc_view:"):
        account_id = data.split(":", 1)[1]
        return await _show_account_detail(update, context, account_id)

    return PAGE_VIEWING


async def _show_account_detail(update, context, account_id: str):
    query = update.callback_query
    account = await get_account_by_id(account_id)
    if not account:
        await safe_edit(query, "❌ Account not found.", reply_markup=main_menu_kb())
        return ConversationHandler.END

    context.user_data["detail_account_id"] = account_id
    await _disconnect_detail(context, account_id)

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
            context.user_data[f"detail_client_{account_id}"] = client
        except Exception as e:
            await _disconnect_detail(context, account_id)
            info_text = (
                f"👤 **Account Detail**\n\n"
                f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
                f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
                f"├─ **User ID** : `{account.get('user_id', '?')}`\n\n"
                f"⚠️ Could not fetch live data: {e}"
            )
    else:
        info_text = (
            f"👤 **Account Detail**\n\n"
            f"├─ **Name**    : {account.get('name', 'Unknown')}\n"
            f"├─ **Phone**   : `{account.get('phone', 'Unknown')}`\n"
            f"├─ **User ID** : `{account.get('user_id', '?')}`\n\n"
            "⚠️ Session expired."
        )

    await safe_edit(query, info_text, parse_mode="Markdown", reply_markup=account_detail_kb(account_id))
    return ACCOUNT_DETAIL


async def account_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("acc_otp:"):
        account_id = data.split(":", 1)[1]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await safe_edit(query, "❌ Session expired.", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        await safe_edit(query, "🔍 Fetching OTP (this can take ~30s)...")
        account = await get_account_by_id(account_id)
        last_otp = account.get("last_otp") if account else None
        otp = await fetch_otp(client, attempts=8, delay=3.0)

        if otp:
            await set_last_otp(account_id, otp)
            tag = " (same as last time)" if last_otp == otp else ""
            text = (
                f"✅ **OTP Found!{tag}**\n\n"
                f"Account: **{account.get('name', 'Unknown')}**\n"
                f"Phone: `{account.get('phone', 'Unknown')}`\n\n"
                f"📨 **Code:** `{otp}`"
            )
        elif last_otp:
            text = (
                f"ℹ️ **No new OTP found.**\n\n"
                f"Account: **{account.get('name', 'Unknown')}**\n"
                f"Phone: `{account.get('phone', 'Unknown')}`\n\n"
                f"📨 **Last OTP:** `{last_otp}`"
            )
        else:
            text = "❌ No OTP found in recent messages."

        await safe_edit(query, text, parse_mode="Markdown", reply_markup=account_detail_kb(account_id))
        return ACCOUNT_DETAIL

    elif data.startswith("acc_revoke:"):
        account_id = data.split(":", 1)[1]
        account = await get_account_by_id(account_id)

        await safe_edit(query, "🔌 Revoking bot connection...")
        try:
            await _disconnect_detail(context, account_id)
            if account:
                await delete_account(account_id)
                manager = GuardManager(context.application)
                await manager.stop_for_user(user_id, account_uid=account.get("user_id"), notify=False)
            await safe_edit(query, 
                "✅ **Bot connection revoked and account removed.**",
                reply_markup=main_menu_kb(),
            )
            return ConversationHandler.END
        except Exception as e:
            await safe_edit(query, f"❌ Error: {e}", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

    elif data.startswith("acc_allow:"):
        account_id = data.split(":", 1)[1]
        client = context.user_data.get(f"detail_client_{account_id}")
        if not client or not client.is_connected():
            await safe_edit(query, "❌ Session expired.", reply_markup=account_detail_kb(account_id))
            return ACCOUNT_DETAIL

        allow_until = datetime.now(timezone.utc) + timedelta(seconds=ALLOW_LOGIN_SECONDS)
        account = await get_account_by_id(account_id)
        await update_account(account_id, {"guard_allow_until": allow_until, "guard_active": True})

        # Also tell any running guard to honour this window
        manager = GuardManager(context.application)
        manager.allow_login(user_id, account.get("user_id", 0), allow_until)

        await safe_edit(query, 
            f"🔓 **Login Allowed for {ALLOW_LOGIN_SECONDS}s**\n\n"
            "Anyone can log into this account within the window.\n"
            "After that, guard mode will reactivate automatically.",
            parse_mode="Markdown",
            reply_markup=account_detail_kb(account_id),
        )
        return ACCOUNT_DETAIL

    return ACCOUNT_DETAIL


async def back_to_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    account_id = context.user_data.get("detail_account_id")
    if account_id:
        await _disconnect_detail(context, account_id)
        context.user_data.pop("detail_account_id", None)

    page = context.user_data.get("accounts_page", 0)
    return await _show_accounts_page(update, context, user_id, page)


async def back_main_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to the main menu, disconnecting any detail client first."""
    query = update.callback_query
    await query.answer()

    account_id = context.user_data.get("detail_account_id")
    if account_id:
        await _disconnect_detail(context, account_id)
        context.user_data.pop("detail_account_id", None)

    from handlers.start import WELCOME_TEXT
    await safe_edit(query, WELCOME_TEXT, parse_mode="Markdown", reply_markup=main_menu_kb())
    return ConversationHandler.END


def register(application):
    application.add_handler(CallbackQueryHandler(my_accounts_entry, pattern=r"^my_accounts$"))
    application.add_handler(CallbackQueryHandler(accounts_navigation, pattern=r"^acc_page:|^acc_refresh$|^acc_view:"))
    application.add_handler(CallbackQueryHandler(account_actions, pattern=r"^acc_otp:|^acc_revoke:|^acc_allow:"))
    application.add_handler(CallbackQueryHandler(back_to_accounts, pattern=r"^acc_back$"))
    application.add_handler(CallbackQueryHandler(back_main_cleanup, pattern=r"^back_main$"))
