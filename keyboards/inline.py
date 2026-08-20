from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Callback-data convention: values are separated with ":" (never "|", which is a
# regex metacharacter and has caused silent handler-swallowing bugs).


def main_menu_kb():
    kb = [
        [
            InlineKeyboardButton("🔑 Manage Account", callback_data="manage_account"),
            InlineKeyboardButton("🛡️ Safe / Guard", callback_data="guard_account"),
        ],
        [InlineKeyboardButton("👤 My Accounts", callback_data="my_accounts")],
    ]
    return InlineKeyboardMarkup(kb)


def admin_back_kb():
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")]]
    return InlineKeyboardMarkup(kb)


# ── Manage dashboard ─────────────────────────────────────────────────────────
def manage_dashboard_kb():
    kb = [
        [
            InlineKeyboardButton("📱 Device Dashboard", callback_data="mng_devices"),
            InlineKeyboardButton("🗑️ Clear All", callback_data="mng_clear_all"),
        ],
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data="mng_fetch_otp"),
            InlineKeyboardButton("📧 Change Mail", callback_data="mng_change_mail"),
        ],
        [InlineKeyboardButton("🧪 Check Mail", callback_data="mail_check")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(kb)


def device_dashboard_kb(devices: list):
    """One button per device + revoke-all + revoke-bot + back."""
    kb = []
    for i, dev in enumerate(devices):
        model = (dev.get("device_model") or "Unknown")[:18]
        platform = (dev.get("platform") or "")[:12]
        cur = " ✅" if dev.get("current") else ""
        label = f"📱 {i + 1}. {model} · {platform}{cur}"
        kb.append([InlineKeyboardButton(label, callback_data=f"dev:{i}")])

    kb.append([
        InlineKeyboardButton("🔌 Terminate All Other Sessions", callback_data="revoke_all"),
    ])
    kb.append([
        InlineKeyboardButton("🔴 Revoke Bot Connection", callback_data="revoke_bot"),
    ])
    kb.append([
        InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash"),
    ])
    return InlineKeyboardMarkup(kb)


def terminate_confirm_kb(device_idx: int):
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Terminate", callback_data=f"dev_yes:{device_idx}"),
            InlineKeyboardButton("❌ Cancel", callback_data="dev_no"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def revoke_bot_confirm_kb():
    kb = [
        [
            InlineKeyboardButton("🔴 Yes, Revoke & Remove", callback_data="revoke_bot_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="dev_no"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def clear_all_confirm_kb():
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Clear Everything", callback_data="clr_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="clr_no"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def otp_menu_kb():
    kb = [
        [InlineKeyboardButton("📨 Read Latest OTP", callback_data="otp_read")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash")],
    ]
    return InlineKeyboardMarkup(kb)


def back_to_dashboard_kb():
    kb = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash")]]
    return InlineKeyboardMarkup(kb)


def cancel_kb(action: str):
    kb = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{action}")]]
    return InlineKeyboardMarkup(kb)


def change_mail_prompt_kb():
    """Prompt for email/app-password + a quick mail checker."""
    kb = [
        [InlineKeyboardButton("🧪 Check Saved Mail", callback_data="mail_check")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_change_mail")],
    ]
    return InlineKeyboardMarkup(kb)


# ── My Accounts ──────────────────────────────────────────────────────────────
def accounts_pagination_kb(accounts: list, page: int, total_pages: int):
    kb = []
    start = page * 5
    end = min(start + 5, len(accounts))

    for i in range(start, end):
        acc = accounts[i]
        label = f"📱 {acc.get('name', 'Unknown')} ({acc.get('phone', 'Unknown')})"
        kb.append([InlineKeyboardButton(label, callback_data=f"acc_view:{acc['_id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"acc_page:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"acc_page:{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="acc_refresh")])
    kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")])
    return InlineKeyboardMarkup(kb)


def account_detail_kb(account_id: str):
    kb = [
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data=f"acc_otp:{account_id}"),
            InlineKeyboardButton("🔌 Revoke Bot", callback_data=f"acc_revoke:{account_id}"),
        ],
        [
            InlineKeyboardButton("🔓 Allow Login (60s)", callback_data=f"acc_allow:{account_id}"),
        ],
        [InlineKeyboardButton("🔙 Back to Accounts", callback_data="acc_back")],
    ]
    return InlineKeyboardMarkup(kb)


# ── Guard ────────────────────────────────────────────────────────────────────
def guard_back_kb():
    """Shown while a guard is running (after the conversation ends)."""
    kb = [
        [InlineKeyboardButton("🛡️ Guard Status", callback_data="guard_status")],
        [InlineKeyboardButton("🛑 Deactivate Guard", callback_data="guard_deactivate")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(kb)
