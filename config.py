import os

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int = 0) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = _get_int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "").strip()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017").strip()
DB_NAME = os.getenv("DB_NAME", "session_manager").strip()

OWNER_IDS = [
    int(x.strip())
    for x in os.getenv("OWNER_IDS", "").split(",")
    if x.strip()
]

# Guard loop — how often the account is checked for new (unauthorised) logins.
# Telegram rate-limits GetAuthorizationsRequest, so keep this >= 5 seconds.
GUARD_INTERVAL = _get_int("GUARD_INTERVAL", 5) or 5

# How long the temporary "Allow Login" window lasts (seconds).
ALLOW_LOGIN_SECONDS = _get_int("ALLOW_LOGIN_SECONDS", 60) or 60

# Email/IMAP settings used by the Change Mail + Mail Checker features.
IMAP_TIMEOUT_SECONDS = _get_int("IMAP_TIMEOUT_SECONDS", 15) or 15
