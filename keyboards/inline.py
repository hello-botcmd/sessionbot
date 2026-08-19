from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_kb():
    """Main menu with 3 buttons: 2 in top row, 1 below."""
    kb = [
        [
            InlineKeyboardButton("🔑 Manage Account", callback_data="manage_account"),
            InlineKeyboardButton("🛡️ Safe / Guard", callback_data="guard_account"),
        ],
        [InlineKeyboardButton("👤 My Accounts", callback_data="my_accounts")],
    ]
    return InlineKeyboardMarkup(kb)


def manage_dashboard_kb():
    """Manage account dashboard: 2x2 grid + cancel."""
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


def device_dashboard_kb(devices: list, account_id: str):
    """
    Build device dashboard with terminate buttons per device + revoke bot + back.
    """
    kb = []
    for i, dev in enumerate(devices):
        current = " ✅" if dev.get("current") else ""
        label = f"{'📱' if not dev.get('current') else '✅'} Device {i+1}{current}"
        kb.append([InlineKeyboardButton(label, callback_data=f"term_dev_{account_id}_{i}")])

    kb.append([
        InlineKeyboardButton("🔌 Revoke Bot Sessions", callback_data=f"revoke_bot_{account_id}"),
    ])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data=f"mng_back_dash_{account_id}")])
    return InlineKeyboardMarkup(kb)


def terminate_confirm_kb(account_id: str, device_idx: int):
    """Yes / Cancel for device termination."""
    kb = [
        [
            InlineKeyboardButton("✅ Yes, Terminate", callback_data=f"term_yes_{account_id}_{device_idx}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"term_no_{account_id}"),
        ]
    ]
    return InlineKeyboardMarkup(kb)


def otp_menu_kb(account_id: str):
    """OTP: show button to fetch + back."""
    kb = [
        [InlineKeyboardButton("📨 Read OTP", callback_data=f"otp_read_{account_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"mng_back_dash_{account_id}")],
    ]
    return InlineKeyboardMarkup(kb)


def back_to_dashboard_kb(account_id: str):
    """Simple back button."""
    kb = [[InlineKeyboardButton("🔙 Back", callback_data=f"mng_back_dash_{account_id}")]]
    return InlineKeyboardMarkup(kb)


def cancel_kb(action: str):
    """Generic cancel button."""
    kb = [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{action}")]]
    return InlineKeyboardMarkup(kb)


# ── My Accounts Pagination ──────────────────────────────────────────────────
def accounts_pagination_kb(accounts: list, page: int, total_pages: int):
    """
    Build keyboard for My Accounts with pagination.
    Shows max 5 accounts per page + prev/next + refresh.
    """
    kb = []
    start = page * 5
    end = min(start + 5, len(accounts))

    for i in range(start, end):
        acc = accounts[i]
        name = acc.get("name", "Unknown")
        phone = acc.get("phone", "Unknown")
        label = f"📱 {name} ({phone})"
        kb.append([InlineKeyboardButton(label, callback_data=f"acc_view_{acc['_id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"acc_page_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"acc_page_{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="acc_refresh")])
    kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="back_main")])
    return InlineKeyboardMarkup(kb)


def account_detail_kb(account_id: str):
    """Account detail page: Fetch OTP, Revoke, Allow Login."""
    kb = [
        [
            InlineKeyboardButton("📨 Fetch OTP", callback_data=f"acc_otp_{account_id}"),
            InlineKeyboardButton("🔌 Revoke Bot", callback_data=f"acc_revoke_{account_id}"),
        ],
        [
            InlineKeyboardButton("🔓 Allow Login (60s)", callback_data=f"acc_allow_{account_id}"),
        ],
        [InlineKeyboardButton("🔙 Back to Accounts", callback_data="my_accounts")],
    ]
    return InlineKeyboardMarkup(kb)


# ── Guard Account ────────────────────────────────────────────────────────────
def guard_kb():
    """Guard menu buttons."""
    kb = [
        [InlineKeyboardButton("🛡️ Activate Guard", callback_data="guard_activate")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(kb)


def guard_back_kb(account_id: str):
    """Back button from guard."""
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="guard_account")]]
    return InlineKeyboardMarkup(kb)


# ── Admin ────────────────────────────────────────────────────────────────────
def admin_back_kb():
    """Admin back."""
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
    return InlineKeyboardMarkup(kb)
