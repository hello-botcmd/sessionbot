# Telegram Session Manager Bot

A comprehensive Telegram session management bot built with **python-telegram-bot** and **Telethon**, using **MongoDB** for persistent storage.

## Features

### 🔑 Manage Account
- Connect via **hex session string**
- View account dashboard (phone, name, user ID, DC)
- **Device Dashboard** — list all authorized sessions, terminate any device
- **Revoke Bot Sessions** — kill all non-current sessions
- **Clear All** — delete contacts, DMs, groups, and channels
- **Fetch OTP** — read the latest login code from Telegram
- **Change Mail** — set recovery email for 2FA

### 🛡️ Safe / Guard Mode
- Keep your account actively monitored
- **Auto-terminates** any new login within **2 seconds**
- Instant notification when unauthorized access is detected
- Background loop checks every 2s

### 👤 My Accounts
- View all stored accounts in a **paginated list** (5 per page)
- **Quick dashboard** per account (phone, name, devices, spam status)
- **Fetch OTP** for any stored account
- **Revoke** bot connection and remove account data
- **Allow Login (60s)** — temporarily disable guard mode for 60 seconds

### ⚙️ Admin System
- **Multi-owner** support (set in `.env`)
- `/addsudo <userid>` — grant sudo access
- `/rmsudo <userid>` — revoke sudo access
- `/sudolist` — list all sudo users
- `/addmail email app_password` — save login email
- `/rmmail` — remove saved email
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
nano .env   # Fill in your BOT_TOKEN, API_ID, API_HASH, MONGO_URI, OWNER_IDS

# 4. Run the bot
python main.py
