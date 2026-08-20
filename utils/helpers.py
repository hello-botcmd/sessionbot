import asyncio
import imaplib
import email as email_lib
import inspect
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from telegram.error import BadRequest
from telethon import TelegramClient, password as pwd_mod
from telethon.tl import types
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    GetPasswordRequest,
    UpdatePasswordSettingsRequest,
    ConfirmPasswordEmailRequest,
)
from telethon.tl.functions.contacts import (
    DeleteContactsRequest,
    GetContactsRequest,
)
from telethon.tl.types import InputPeerUser, User, Chat, Channel
from telethon.errors import FloodWaitError, RPCError, EmailUnconfirmedError

logger = logging.getLogger(__name__)

# Telegram service-notifications user (login codes arrive from here).
SERVICE_NOTIFICATIONS_ID = 777000

OTP_PATTERNS = [
    re.compile(r"login\s*code\s*[:.]\s*(\d{4,6})", re.IGNORECASE),
    re.compile(r"confirmation\s*code\s*[:.]\s*(\d{4,6})", re.IGNORECASE),
    re.compile(r"\bcode\s*[:.]\s*(\d{4,6})\b", re.IGNORECASE),
]


# ── Safe message editing ────────────────────────────────────────────────────
async def safe_edit(query, text=None, reply_markup=None, parse_mode=None, **kwargs):
    """
    Edit a callback-query message without EVER crashing the button handler.

    - "Message is not modified" → the message already shows this content
      (double-tap); treated as success.
    - Any other edit failure (message too old >48h, message deleted, "can't be
      edited", unknown BadRequest, ...) → send a FRESH message so the user
      always sees a response.

    Returns the edited message, the newly-sent message, or None.
    """
    try:
        return await query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
        )
    except BadRequest as exc:
        msg = str(exc)
        if "not modified" in msg:
            return None

        # Fall back to a fresh message. Guard against a missing message object.
        logger.info("Edit failed (%s); sending a fresh message instead", msg)
        if query.message is None or getattr(query.message, "chat_id", None) is None:
            raise
        bot = query.get_bot()
        return await bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            **kwargs,
        )


# ── Spam status ──────────────────────────────────────────────────────────────
async def check_spam_status(client: TelegramClient) -> str:
    """Check if the account is spam-limited via @SpamBot."""
    try:
        spam_bot = await client.get_entity("@SpamBot")
        await client.send_message(spam_bot, "/start")
        await asyncio.sleep(1.5)
        async for m in client.iter_messages(spam_bot, limit=1):
            if m and m.text:
                t = m.text.lower()
                if any(w in t for w in ("good", "not limited", "no restrictions", "fine")):
                    return "✅ Clean (Not Spam)"
                if any(w in t for w in ("limited", "restricted", "spam")):
                    return "⚠️ Limited / Restricted"
                return f"ℹ️ {m.text[:100]}"
        return "❓ Could not determine"
    except Exception as e:
        logger.warning(f"Spam check failed: {e}")
        return "❓ Check failed"


# ── Devices ──────────────────────────────────────────────────────────────────
async def get_devices(client: TelegramClient, raise_errors: bool = False) -> list:
    """
    Get all authorised sessions (devices).

    If ``raise_errors`` is True, RPC errors (e.g. FloodWaitError) propagate so
    the caller can back off — used by the guard loop. Otherwise errors are
    swallowed and an empty list is returned (safe for UI rendering).
    """
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
                "date_created": a.date_created,   # datetime (naive, UTC)
                "date_active": a.date_active,     # datetime (naive, UTC)
                "current": bool(a.current),
                "official_app": a.official_app,
                "password_pending": a.password_pending,
            })
        return devices
    except Exception as e:
        logger.error(f"Failed to get devices: {e}")
        if raise_errors:
            raise
        return []


async def terminate_device(client: TelegramClient, hash_id: int) -> bool:
    """Terminate a specific device session by its authorization hash."""
    try:
        await client(ResetAuthorizationRequest(hash_id))
        return True
    except Exception as e:
        logger.error(f"Failed to terminate device {hash_id}: {e}")
        return False


# ── Clear all ────────────────────────────────────────────────────────────────
async def clear_all_data(client: TelegramClient) -> dict:
    """
    Clear everything: contacts, private chats (DMs), groups, channels,
    and the saved-messages history.

    Groups/channels are left (delete_dialog handles leaving), DM history is
    deleted with revoke=True so it is removed for both sides.
    """
    result = {"contacts": 0, "dialogs": 0, "left": 0, "errors": 0}

    # 1. Delete all contacts
    try:
        contacts_result = await client(GetContactsRequest(hash=0))
        contacts = getattr(contacts_result, "users", []) or []
        input_users = [
            InputPeerUser(c.id, c.access_hash)
            for c in contacts
            if getattr(c, "access_hash", None) and c.id != SERVICE_NOTIFICATIONS_ID
        ]
        if input_users:
            await client(DeleteContactsRequest(id=input_users))
            result["contacts"] = len(input_users)
    except Exception as e:
        logger.error(f"Clear contacts error: {e}")
        result["errors"] += 1

    # 2. Delete every dialog / leave every group & channel
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            try:
                entity = dialog.entity
                if isinstance(entity, User) and entity.id == SERVICE_NOTIFICATIONS_ID:
                    continue
                if isinstance(entity, User):
                    await client.delete_dialog(entity, revoke=True)
                    result["dialogs"] += 1
                elif isinstance(entity, (Chat, Channel)):
                    await client.delete_dialog(entity, revoke=True)
                    result["left"] += 1
            except FloodWaitError as e:
                logger.warning(f"Clear dialogs flood wait {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except RPCError as e:
                logger.warning(f"Clear dialog error: {e}")
                result["errors"] += 1
            except Exception:
                result["errors"] += 1
    except Exception as e:
        logger.error(f"Clear dialogs error: {e}")
        result["errors"] += 1

    return result


# ── Fetch OTP ────────────────────────────────────────────────────────────────
async def _scan_otp(client: TelegramClient) -> Optional[str]:
    """Scan recent service messages for a login code."""
    try:
        async for msg in client.iter_messages(SERVICE_NOTIFICATIONS_ID, limit=10):
            if not msg or not msg.text:
                continue
            for pattern in OTP_PATTERNS:
                match = pattern.search(msg.text)
                if match:
                    return match.group(1)
    except Exception as e:
        logger.error(f"OTP scan error: {e}")
    return None


async def fetch_otp(client: TelegramClient, attempts: int = 6,
                    delay: float = 3.0) -> Optional[str]:
    """
    Poll Telegram for the latest login code.

    Tries ``attempts`` times, sleeping ``delay`` seconds between attempts, so a
    freshly-requested code has time to arrive. Returns the code or None.
    """
    for i in range(max(1, attempts)):
        code = await _scan_otp(client)
        if code:
            return code
        if i < attempts - 1:
            await asyncio.sleep(delay)
    return None


# ── Change recovery email (Telegram 2FA) ────────────────────────────────────
async def set_recovery_email(client: TelegramClient, email: str,
                             code_callback, current_password: str | None = None) -> bool:
    """
    Set the account's recovery/verification email.

    ``code_callback(code_length)`` must return the verification code Telegram
    emails to ``email`` (it may be async).

    Implemented with the raw API (instead of ``edit_2fa``) so it also works for
    accounts that do NOT have a 2FA password yet — Telethon's ``edit_2fa``
    no-ops when neither password argument is given.
    """
    pwd = await client(GetPasswordRequest())
    pwd.new_algo.salt1 += os.urandom(32)

    if not pwd.has_password:
        current_password = None

    if current_password:
        password = pwd_mod.compute_check(pwd, current_password)
    else:
        password = types.InputCheckPasswordEmpty()

    try:
        await client(UpdatePasswordSettingsRequest(
            password=password,
            new_settings=types.account.PasswordInputSettings(
                new_algo=pwd.new_algo,
                new_password_hash=b"",
                hint="",
                email=email,
                new_secure_settings=None,
            ),
        ))
    except EmailUnconfirmedError as e:
        code = code_callback(e.code_length)
        if inspect.isawaitable(code):
            code = await code
        await client(ConfirmPasswordEmailRequest(str(code)))

    return True


# ── Email / IMAP helpers (Change Mail + Mail Checker) ───────────────────────
def _imap_host(email_address: str) -> str:
    domain = email_address.rsplit("@", 1)[-1].lower().strip()
    return {
        "gmail.com": "imap.gmail.com",
        "googlemail.com": "imap.gmail.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "live.com": "outlook.office365.com",
        "yahoo.com": "imap.mail.yahoo.com",
    }.get(domain, "imap.gmail.com")


def _extract_email_code(body: str) -> Optional[str]:
    """Extract a 4-6 digit verification code from an email body."""
    if not body:
        return None
    for pattern in OTP_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1)
    # Last resort: first standalone 4-6 digit number
    match = re.search(r"(?<!\d)(\d{4,6})(?!\d)", body)
    return match.group(1) if match else None


def _read_email_code_once(email_address: str, app_password: str) -> Optional[str]:
    """Blocking IMAP read of the latest Telegram verification email."""
    mail = imaplib.IMAP4_SSL(_imap_host(email_address), 993)
    try:
        mail.login(email_address, app_password)
        status, _ = mail.select("inbox")
        if status != "OK":
            return None

        status, messages = mail.search(None, '(FROM "telegram.org")')
        if status != "OK" or not messages or not messages[0]:
            return None

        ids = messages[0].split()
        if not ids:
            return None

        # Walk from newest backwards in case the newest isn't the OTP email
        for mail_id in reversed(ids[-3:]):
            status, msg_data = mail.fetch(mail_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_email = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw_email)

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors="ignore")
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(errors="ignore")

            code = _extract_email_code(body)
            if code:
                return code
        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass


async def read_email_otp(email_address: str, app_password: str,
                         wait_seconds: int = 90,
                         poll_interval: float = 4.0) -> Optional[str]:
    """
    Poll the mailbox for a Telegram verification code.

    Keeps trying for ``wait_seconds`` (new codes take a few seconds to arrive).
    IMAP runs in a worker thread so the bot event loop is never blocked.
    """
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        try:
            code = await asyncio.to_thread(_read_email_code_once, email_address, app_password)
        except Exception as e:
            logger.warning(f"IMAP read error: {e}")
            code = None
        if code:
            return code
        await asyncio.sleep(poll_interval)
    return None


def _imap_check(email_address: str, app_password: str) -> dict:
    """Blocking IMAP connectivity/verification check."""
    host = _imap_host(email_address)
    mail = imaplib.IMAP4_SSL(host, 993)
    try:
        mail.login(email_address, app_password)
        status, _ = mail.select("inbox", readonly=True)
        if status != "OK":
            return {"ok": False, "error": "Could not open INBOX", "host": host}

        telegram_emails = 0
        latest = None
        status, data = mail.search(None, '(FROM "telegram.org")')
        if status == "OK" and data and data[0]:
            ids = data[0].split()
            telegram_emails = len(ids)
            st, md = mail.fetch(
                ids[-1], "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])"
            )
            if st == "OK" and md and md[0]:
                latest = md[0][1].decode(errors="ignore").strip()[:160]

        unread = 0
        status, data = mail.search(None, "UNSEEN")
        if status == "OK" and data and data[0]:
            unread = len(data[0].split())

        return {
            "ok": True,
            "host": host,
            "unread": unread,
            "telegram_emails": telegram_emails,
            "latest": latest,
        }
    finally:
        try:
            mail.logout()
        except Exception:
            pass


async def verify_mail(email_address: str, app_password: str) -> dict:
    """Verify an IMAP login works and report mailbox state."""
    try:
        result = await asyncio.to_thread(_imap_check, email_address, app_password)
        result.setdefault("email", email_address)
        return result
    except imaplib.IMAP4.error as e:
        return {"ok": False, "email": email_address, "error": f"IMAP login failed: {e}"}
    except Exception as e:
        return {"ok": False, "email": email_address, "error": str(e)}


# ── Formatting ───────────────────────────────────────────────────────────────
def _to_utc_datetime(value) -> Optional[datetime]:
    """Normalise a datetime|timestamp|None into a tz-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _ago(value) -> str:
    dt = _to_utc_datetime(value)
    if dt is None:
        return "Unknown"
    diff = datetime.now(timezone.utc) - dt
    total = int(diff.total_seconds())
    if total < 0:
        total = 0
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0:
        return f"{d}d {h}h ago"
    if h > 0:
        return f"{h}h {m}m ago"
    if m > 0:
        return f"{m}m ago"
    return "just now"


def format_account_info(info: dict) -> str:
    """Format account info for bot messages."""
    name = f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
    username = info.get("username", "")
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
    """Format device info (handles datetime & timestamp alike)."""
    current_mark = " ✅ CURRENT" if dev.get("current") else ""
    app = f"{dev.get('app_name', '')} {dev.get('app_version', '')}".strip()
    ip = dev.get("ip") or ""
    region = f"{dev.get('region', '')} {dev.get('country', '')}".strip()
    official = "Yes" if dev.get("official_app") else "No"

    return (
        f"📱 **Device #{index + 1}**{current_mark}\n"
        f"├─ Model    : {dev.get('device_model', 'Unknown')}\n"
        f"├─ Platform : {dev.get('platform', 'Unknown')}\n"
        f"├─ App      : {app or 'Unknown'} (official: {official})\n"
        f"├─ IP       : {ip or 'Unknown'}\n"
        f"├─ Region   : {region or 'Unknown'}\n"
        f"├─ Active   : {_ago(dev.get('date_active'))}\n"
        f"└─ Created  : {_ago(dev.get('date_created'))}\n"
    )
