import binascii
import struct
import base64
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.crypto import AuthKey
from telethon.errors import FloodWaitError, RPCError
import asyncio
import logging

logger = logging.getLogger(__name__)

# Telegram datacenter IPs (official)
DC_IPS = {
    1: ("149.154.175.50", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),
    5: ("91.108.56.151", 443),
}


def parse_hex_session(hex_string: str):
    """
    Parse a hex-encoded Telegram session into a Telethon StringSession.

    Handles multiple formats:
    1. Hex-encoded Telethon StringSession (base64)
    2. Hex-encoded auth_key (256 bytes raw) — fallback with DC auto-detect
    3. Raw base64 StringSession
    """
    hex_string = hex_string.strip()

    # If it's already a base64 string session (starts with '1' + base64 chars)
    if hex_string[0] in ("1", "2") and all(
        c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=-_" for c in hex_string
    ):
        return _try_create_client_from_string(hex_string)

    # Try as hex string
    try:
        raw_bytes = binascii.unhexlify(hex_string)
    except binascii.Error:
        return None, "Invalid hex string: could not decode."

    # Try interpret as base64-encoded session string (hex -> bytes -> utf-8)
    try:
        as_text = raw_bytes.decode("utf-8", errors="ignore")
        if as_text and as_text[0] in ("1", "2") and len(as_text) > 10:
            # Looks like a base64 session string
            return _try_create_client_from_string(as_text)
    except Exception:
        pass

    # Try interpret as raw auth_key (256 bytes exactly) + possible DC prefix
    return _try_create_client_from_auth_key(raw_bytes)


def _try_create_client_from_string(session_string: str):
    """Try to create a TelegramClient from a StringSession string."""
    try:
        client = TelegramClient(StringSession(session_string), 0, "")  # temp
        # We'll set api_id/hash later, just validate format
        return client, session_string
    except Exception as e:
        return None, f"Invalid session string: {e}"


def _try_create_client_from_auth_key(raw_bytes: bytes):
    """Try to create a TelegramClient from raw auth_key bytes."""
    try:
        if len(raw_bytes) not in (256, 257, 258):
            return None, (
                f"Invalid auth key length: {len(raw_bytes)} bytes. "
                "Expected 256 bytes for auth_key."
            )

        auth_key_bytes = raw_bytes[-256:]  # Take last 256 bytes as auth_key
        dc_id = raw_bytes[0] if len(raw_bytes) > 256 and raw_bytes[0] in DC_IPS else 2

        ip, port = DC_IPS.get(dc_id, DC_IPS[2])

        session = StringSession()
        session.set_dc(dc_id, ip, port)
        session.auth_key = AuthKey(auth_key_bytes)

        client = TelegramClient(session, 0, "")
        return client, None  # success, no additional string

    except Exception as e:
        return None, f"Failed to create session from auth key: {e}"


async def verify_and_get_client(hex_string: str, api_id: int, api_hash: str):
    """
    Parse hex, create Telethon client, verify it works, return (client, account_info).
    Returns (client, info_dict) on success, (None, error_msg) on failure.
    """
    client, session_or_error = parse_hex_session(hex_string)
    if client is None:
        return None, session_or_error

    client.api_id = api_id
    client._api_id = api_id
    client.api_hash = api_hash
    client._api_hash = api_hash

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None, "Session is not authorized. The auth key may be expired or invalid."

        me = await client.get_me()
        if me is None:
            await client.disconnect()
            return None, "Could not fetch user info. Session may be invalid."

        # Get the string session for storage
        saved_string = client.session.save() if hasattr(client.session, "save") else ""

        info = {
            "id": me.id,
            "phone": getattr(me, "phone", "Unknown"),
            "first_name": getattr(me, "first_name", ""),
            "last_name": getattr(me, "last_name", ""),
            "username": getattr(me, "username", ""),
            "dc_id": client.session.dc_id if hasattr(client.session, "dc_id") else 0,
            "session_string": saved_string,
            "hex_input": hex_string,
        }
        return client, info

    except FloodWaitError as e:
        if client.is_connected():
            await client.disconnect()
        return None, f"Flood wait error: need to wait {e.seconds} seconds."
    except RPCError as e:
        if client.is_connected():
            await client.disconnect()
        return None, f"Telegram RPC error: {e}"
    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        return None, f"Connection error: {e}"
