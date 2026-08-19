import asyncio
import imaplib
import email as email_lib
import logging
import re
import time
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    DeleteHistoryRequest,
)
from telethon.tl.functions.contacts import DeleteContactsRequest
from telethon.tl.types import InputPeerUser, User, Chat, Channel
from telethon.errors import RPCError

logger = logging.getLogger(__name__)


async def check_spam_status(client: TelegramClient) -> str:
    """Check if account is spam-limited via @SpamBot."""
    try:
        spam_bot = await client.get_entity("@SpamBot")
        msg = await client.send_message(spam_bot, "/start")
        await asyncio.sleep(1.5)
        async for m in client.iter_messages(spam_bot, limit=1):
            if m.text:
                t = m.text.lower()
                if any(w in t for w in ("good", "not limited", "no restrictions", "fine")):
                    return "✅ Clean (Not Spam)"
                elif any(w in t for w in ("limited", "restricted", "spam")):
                    return "⚠️ Limited / Restricted"
                else:
                    return f"ℹ️ {m.text[:100]}"
        return "❓ Could not determine"
    except Exception as e:
        logger.warning(f"Spam check failed: {e}")
        return "❓ Check failed"


async def get_devices(client: TelegramClient) -> list:
    """Get all active authorized sessions (devices)."""
    try:
        auths = await client(GetAuthorizationsRequest())
        devices = []
        for a in auths.authorizations:
            devices.append({
                "hash": a.hash,
                "device_model": a.device_model or "Unknown",
                "platform": a.platform or "Unknown",
                "app_name": a.app_name or "Unknown",
                "app_version": a.app_version or "",
                "ip": a.ip or "",
                "country": a.country or "",
                "region": a.region or "",
                "date_created": a.date_created,
                "date_active": a.date_active,
                "current": a.current,
                "official_app": a.official_app,
                "password_pending": a.password_pending,
            })
        return devices
    except Exception as e:
        logger.error(f"Failed to get devices: {e}")
        return []


async def terminate_device(client: TelegramClient, hash_id: int) -> bool:
    """Terminate a specific device session."""
    try:
        await client(ResetAuthorizationRequest(hash_id))
        return True
    except Exception as e:
        logger.error(f"Failed to terminate device {hash_id}: {e}")
        return False


async def clear_all_data(client: TelegramClient) -> dict:
    """Clear all contacts, dialogs, groups, channels."""
    result = {"contacts": 0, "dialogs": 0, "errors": 0}

    # 1. Delete all contacts
    try:
        contacts = await client.get_contacts()
        if contacts:
            input_users = [
                InputPeerUser(c.id, c.access_hash)
                for c in contacts if hasattr(c, 'access_hash') and c.access_hash
            ]
            if input_users:
                await client(DeleteContactsRequest(id=input_users))
                result["contacts"] = len(input_users)
    except Exception as e:
        logger.error(f"Clear contacts error: {e}")
        result["errors"] += 1

    # 2. Delete all dialogs
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            try:
                entity = dialog.entity
                if isinstance(entity, User) and entity.id != 777000:
                    await client.delete_dialog(entity, revoke=True)
                    result["dialogs"] += 1
                elif isinstance(entity, (Chat, Channel)):
                    await client.delete_dialog(entity, revoke=True)
                    result["dialogs"] += 1
            except Exception:
                result["errors"] += 1
                continue
    except Exception as e:
        logger.error(f"Clear dialogs error: {e}")
        result["errors"] += 1

    return result


async def fetch_otp(client: TelegramClient) -> str:
    """Fetch the latest OTP from Telegram service messages (user 777000)."""
    try:
        async for msg in client.iter_messages(777000, limit=5):
            if msg.text:
                match = re.search(r'Login code[:.\s]*(\d{4,7})', msg.text, re.IGNORECASE)
                if match:
                    return match.group(1)
                match = re.search(r'(\d{4,7})', msg.text)
                if match and ("code" in msg.text.lower() or "login" in msg.text.lower()):
                    return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Fetch OTP error: {e}")
        return None


async def read_email_otp(email_address: str, app_password: str,
                         sender_email: str = "noreply@telegram.org",
                         wait_seconds: int = 15) -> str | None:
    """
    Connect to Gmail IMAP, find the latest email from Telegram, extract code.

    Returns the OTP code string or None.
    """
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_address, app_password)
        mail.select("inbox")

        # Wait a bit for the email to arrive
        await asyncio.sleep(3)

        # Search for unread emails from Telegram
        status, messages = mail.search(None, '(UNSEEN FROM "telegram.org")')
        if status != "OK" or not messages[0]:
            status, messages = mail.search(None, '(FROM "telegram.org")')

        if status != "OK" or not messages[0]:
            mail.logout()
            return None

        # Get the latest message
        latest = messages[0].split()[-1]
        status, msg_data = mail.fetch(latest, "(RFC822)")
        if status != "OK":
            mail.logout()
            return None

        raw_email = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw_email)

        # Extract body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        mail.logout()

        # Extract code from body - look for patterns like "12345" near "code"
        code_match = re.search(r'(\b\d{4,7}\b)', body)
        if code_match:
            return code_match.group(1)

        return None

    except Exception as e:
        logger.error(f"IMAP email read error: {e}")
        return None


def format_account_info(info: dict) -> str:
    """Format account info for bot messages."""
    name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
    username = info.get('username', '')
    text = (
        f"👤 **Account Dashboard**\n\n"
        f"├─ **Name**    : {name or 'Unknown'}\n"
        f"├─ **Phone**   : `{info.get('phone', 'Unknown')}`\n"
        f"├─ **User ID** : `{info.get('id', 'Unknown')}`\n"
        f"├─ **DC**      : DC-{info.get('dc_id', '?')}\n"
    )
    if username:
        text += f"├─ **Username**: @{username}\n"
    return text


def format_device(dev: dict, index: int = 0) -> str:
    """Format device info."""
    current_mark = " ✅ CURRENT" if dev.get("current") else ""
    app = f"{dev.get('app_name', '')} {dev.get('app_version', '')}".strip()
    active_ts = dev.get("date_active", 0)
    created_ts = dev.get("date_created", 0)

    def _ago(ts):
        if not ts:
            return "Unknown"
        diff = datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)
        d, h, m = diff.days, diff.seconds // 3600, (diff.seconds % 3600) // 60
        if d > 0: return f"{d}d ago"
        if h > 0: return f"{h}h {m}m ago"
        return f"{m}m ago"

    return (
        f"📱 **Device #{index + 1}**{current_mark}\n"
        f"├─ Model    : {dev.get('device_model', 'Unknown')}\n"
        f"├─ Platform : {dev.get('platform', 'Unknown')}\n"
        f"├─ App      : {app or 'Unknown'}\n"
        f"├─ IP       : {dev.get('ip', '') or ''}\n"
        f"├─ Region   : {dev.get('region', '')} {dev.get('country', '')}\n"
        f"├─ Active   : {_ago(active_ts)}\n"
        f"└─ Created  : {_ago(created_ts)}\n"
    )
