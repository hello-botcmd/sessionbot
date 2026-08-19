# Telegram Session Manager Bot

A comprehensive Telegram session management bot built with **python-telegram-bot**
and **Telethon**, using **MongoDB** for persistent storage.

## Features

### 🔑 Manage Account
- Connect via **Telethon StringSession**, **Pyrogram session string**, raw
  **256-byte auth_key hex** (DC auto-probed 5→4→3→2→1), or a `.session` file.
- **Account dashboard** — phone, name, user ID, DC, spam status.
- **📱 Device Dashboard** — every authorised device listed as a button; tap a
  device to **terminate that session**, plus **Terminate All Other Sessions**
  and **Revoke Bot Connection** (disconnect + remove stored account).
- **🗑️ Clear All** — delete contacts, DMs, leave groups/channels, and clear
  saved messages. Confirmed via **inline buttons** (no typing "YES").
- **📨 Fetch OTP** — polls Telegram multiple times for the latest login code and
  remembers the **last OTP** so you can re-display it.
- **📧 Change Mail** — sets the account's recovery/2FA email: Telegram sends a
  verification code to your mailbox, the bot reads it automatically via IMAP and
  confirms it (works for accounts with or without an existing 2FA password).
- **🧪 Check Mail** — verifies your saved mail works and reports a verification
  message.

### 🛡️ Safe / Guard Mode
- Keeps the session alive and **auto-terminates any new login** (respecting
  Telegram's flood limits).
- Instant notification of every terminated intruder.
- Status + deactivate buttons, plus `/guardstatus` and `/stopguard`.
- The "Allow Login (60s)" window is honoured by the running guard.

### 👤 My Accounts
- Paginated list (5 per page) of all stored accounts.
- Per-account detail: device count, spam status, **Fetch OTP** (with last-OTP
  memory), **Revoke Bot**, and **Allow Login (60s)**.

### ⚙️ Admin System
- **Multi-owner** support (set in `.env`).
- `/addsudo <userid>` · `/rmsudo <userid>` · `/sudolist`
- `/addmail email app_password` — save **and verify** a login mail
- `/checkmail` — mail checker (verification message)
- `/mymail` — view saved mail · `/rmmail` — remove saved mail
- `/help` — show all commands

## Requirements

- Python 3.10+
- MongoDB (local or remote)
- Telegram API ID & Hash (from [my.telegram.org](https://my.telegram.org))

## Installation

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd telegram-session-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
nano .env   # Fill in BOT_TOKEN, API_ID, API_HASH, MONGO_URI, OWNER_IDS

# 4. Run the bot
python main.py
```

## Notes

- **Gmail / Outlook / Yahoo** app passwords are supported for the change-mail
  and mail-checker features (an *app password*, not your account password, is
  required).
- Changing the recovery email on an account that already has 2FA will ask you
  for that account's **2FA password**.
- Guard mode runs in-memory; it is stopped cleanly on bot shutdown. Reconnect
  the session and re-activate guard after a restart.

> **Disclaimer:** this tool is for managing your own sessions. Misuse against
> accounts you do not own may violate Telegram's Terms of Service.
