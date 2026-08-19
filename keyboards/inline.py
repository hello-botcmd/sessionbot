from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb():
    kb = [
        [
            InlineKeyboardButton("🔑 Manage Account", callback_data="manage_account"),
            InlineKeyboardButton("🛡️ Safe / Guard", callback_data="guard_account"),
        ],
        [InlineKeyboardButton("👤 My Accounts", callback_data="my_accounts")],
    ]
    return InlineKeyboardMarkup(kb)


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
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(kb)


def device_dashboard_kb(device_count: int):
    """
    Build device dashboard with one button per device + revoke bot + back.
    Uses pipe separator in callback data to avoid underscore ambiguity.

    Callback format: term|{idx}
    """
    kb = []
    for i in range(device_count):
        label = f"📱 Device #{i + 1} — Tap to Terminate"
        kb.append([InlineKeyboardButton(label, callback_data=f"term|{i}")])

    kb.append([InlineKeyboardButton("🔌 Revoke All Bot Sessions", callback_data="revoke_bot")])
    kb.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash")])
    return InlineKeyboardMarkup(kb)


def terminate_confirm_kb(device_idx: int):
    """Yes / Cancel for device termination."""
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Terminate", callback_data=f"term_yes|{device_idx}"),
            InlineKeyboardButton("❌ Cancel", callback_data="term_no"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def clear_all_confirm_kb():
    """Yes / Cancel for clear all."""
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Clear Everything", callback_data="clr_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="clr_no"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def otp_menu_kb():
    """OTP: show fetch button + back."""
    kb = [
        [InlineKeyboardButton("📨 Read Latest OTP", callback_data="otp_read")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash")],
    ]
    return InlineKeyboardMarkup(kb)


def back_to_dashboard_kb():
    """Simple back to dashboard."""
    kb = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="mng_back_dash")]]
    return InlineKeyboardMarkup(kb)


def cancel_kb(action: str):
    """Generic cancel button."""
    kb = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{action}")]]
    return InlineKeyboardMarkup(kb)


# ── My Accounts Pagination ──────────────────────────────────────────────────
def accounts_pagination_kb(accounts: list, page: int, total_pages: int):
    """Paginated account list (5 per page)."""
    kb = []
    start = page * 5
    end = min(start + 5, len(accounts))

    for i in range(start, end):
        acc = accounts[i]
        label = f"📱 {acc.get('name', 'Unknown')} ({acc.get('phone', 'Unknown')})"
        kb.append([InlineKeyboardButton(label, callback_data=f"acc_view|{acc['_id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"acc_page|{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"acc_page|{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="acc_refresh")])
    kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")])
    return InlineKeyboardMarkup(kb)


def account_detail_kb(account_id: str):
    """Account detail: Fetch OTP, Revoke, Allow Login (uses | separator)."""
    kb = [
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data=f"acc_otp|{account_id}"),
            InlineKeyboardButton("🔌 Revoke Bot", callback_data=f"acc_revoke|{account_id}"),
        ],
        [
            InlineKeyboardButton("🔓 Allow Login (60s)", callback_data=f"acc_allow|{account_id}"),
        ],
        [InlineKeyboardButton("🔙 Back to Accounts", callback_data="acc_back")],
    ]
    return InlineKeyboardMarkup(kb)


# ── Guard ───────────────────────────────────────────────────────────────────
def guard_kb():
    kb = [
        [InlineKeyboardButton("🛡️ Activate Guard", callback_data="guard_activate")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(kb)


def guard_back_kb():
    """Back button from guard."""
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")]]
    return InlineKeyboardMarkup(kb)


# ── Change Mail ─────────────────────────────────────────────────────────────
def change_mail_kb():
    kb = [
        [InlineKeyboardButton("📧 Start Mail Change", callback_data="mail_start")]
    ]
    return InlineKeyboardMarkup(kb)
