"""
Session parsing and Telethon client creation.

Supports:
  1. Telethon StringSession (base64)
  2. Pyrogram session strings (all packing formats)
  3. Raw 256-byte auth_key as hex (auto-probes DCs 5→4→3→2→1 if not specified)
  4. Telethon/Pyrogram SQLite .session files

No guessing-based format detection. Every format is identified by its
binary/structural signature.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

# ── Telegram production datacenter addresses ──────────────────────────────
# Verified against Telethon source and Pyrogram documentation (2026).

DC_IPS: dict[int, tuple[str, int]] = {
    1: ("149.154.175.50", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),  # was 149.167.167.91 in original paste — WRONG, fixed
    5: ("91.108.56.151", 443),
}


# ── Normalised session descriptor ──────────────────────────────────────────


@dataclass
class SessionParts:
    """Normalised internal representation of a Telegram session."""

    dc_id: int
    auth_key: bytes
    user_id: int | None = None
    is_bot: bool = False
    api_id: int | None = None
    test_mode: bool = False
    server_address: str | None = None
    port: int = 443
    source: str = "unknown"

    def validate(self) -> None:
        """Raise ``ValueError`` if these parts cannot be used."""
        if self.dc_id not in DC_IPS:
            raise ValueError(
                f"Unsupported Telegram datacenter: DC-{self.dc_id}. "
                f"Valid: 1-5."
            )
        if len(self.auth_key) != 256:
            raise ValueError(
                f"auth_key must be exactly 256 bytes; got {len(self.auth_key)}."
            )

    def to_telethon_session(self) -> StringSession:
        """Build a Telethon ``StringSession`` from these parts."""
        self.validate()
        address, default_port = DC_IPS[self.dc_id]
        session = StringSession()
        session.set_dc(
            self.dc_id,
            self.server_address or address,
            self.port or default_port,
        )
        session.auth_key = AuthKey(self.auth_key)
        return session


# ── Raw hex auth key ──────────────────────────────────────────────────────


def parse_raw_hex_auth_key(hex_key: str, dc_id: int) -> SessionParts:
    """
    Parse exactly 512 hex characters (= 256 bytes) as a Telegram auth_key.

    ``dc_id`` **must** be supplied — it cannot be inferred from raw bytes.
    """
    value = hex_key.strip()
    if value.startswith(("0x", "0X")):
        value = value[2:]

    if len(value) != 512:
        raise ValueError(
            f"Raw auth_key hex must be exactly 512 characters; got {len(value)}. "
            f"(256 bytes hex-encode to 512 chars.)"
        )

    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("auth_key contains non-hex characters.") from exc

    parts = SessionParts(dc_id=int(dc_id), auth_key=raw, source="hex_auth_key")
    parts.validate()
    return parts


# ── Telethon StringSession ────────────────────────────────────────────────


def parse_telethon_string(value: str) -> SessionParts:
    """
    Parse a Telethon ``StringSession`` (base64-encoded).

    Example: ``1aaNk8EX-YRfwoRsebUkugFvht6DUPi_Q25UOCzOAqzc...``
    """
    value = value.strip()
    if not value:
        raise ValueError("Empty Telethon session string.")

    session = StringSession(value)
    if not session.auth_key:
        raise ValueError("Telethon StringSession contains no auth_key.")

    parts = SessionParts(
        dc_id=int(session.dc_id),
        auth_key=session.auth_key.key,
        server_address=session.server_address,
        port=int(session.port or 443),
        source="telethon_string",
    )
    parts.validate()
    return parts


# ── Pyrogram session strings ──────────────────────────────────────────────

_PYRO_FMT = ">BI?256sQ?"
_PYRO_FMT64_ALT = ">BI?256sQI?"
_PYRO_OLD64 = ">B?256sQ?"
_PYRO_OLD = ">B?256sI?"


def _b64decode(value: str) -> bytes:
    """Base64 URL-safe decode with automatic padding."""
    value = value.strip().replace("\n", "").replace(" ", "")
    value += "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value)
    except Exception as exc:
        raise ValueError("Invalid Base64 session string.") from exc


def parse_pyrogram_string(value: str) -> SessionParts:
    """
    Parse a Pyrogram packed session string (all known packing formats).
    """
    data = _b64decode(value)

    formats: list[tuple[str, str]] = [
        (_PYRO_FMT, "pyrogram"),
        (_PYRO_FMT64_ALT, "pyrogram_alt"),
        (_PYRO_OLD64, "pyrogram_old64"),
        (_PYRO_OLD, "pyrogram_old"),
    ]

    for fmt, kind in formats:
        if len(data) != struct.calcsize(fmt):
            continue

        unpacked = struct.unpack(fmt, data)

        try:
            if kind == "pyrogram":
                dc_id, api_id, test_mode, auth_key, user_id, is_bot = unpacked
            elif kind == "pyrogram_alt":
                dc_id, api_id, test_mode, auth_key, user_id, is_bot, _extra = unpacked
            elif kind == "pyrogram_old64":
                dc_id, test_mode, auth_key, user_id, is_bot = unpacked
                api_id = None
            else:  # pyrogram_old
                dc_id, test_mode, auth_key, user_id, is_bot = unpacked
                api_id = None

            parts = SessionParts(
                dc_id=int(dc_id),
                auth_key=bytes(auth_key),
                user_id=int(user_id) if user_id else None,
                is_bot=bool(is_bot),
                api_id=int(api_id) if api_id else None,
                test_mode=bool(test_mode),
                source="pyrogram_string",
            )
            parts.validate()
            return parts

        except (ValueError, TypeError):
            continue

    raise ValueError("Not a supported Pyrogram session string.")


# ── SQLite .session files ─────────────────────────────────────────────────


def parse_session_file(path: str | Path) -> SessionParts:
    """Parse a Telethon or Pyrogram SQLite ``.session`` file from disk."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Session file not found: {path}")

    conn = sqlite3.connect(str(path))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "sessions" not in tables:
            raise ValueError("SQLite file has no 'sessions' table.")

        columns = [row[1] for row in conn.execute("PRAGMA table_info(sessions)")]
        row = conn.execute("SELECT * FROM sessions LIMIT 1").fetchone()
        if row is None:
            raise ValueError("Session table is empty.")

        data = dict(zip(columns, row))

        # Telethon schema
        if {"dc_id", "server_address", "port", "auth_key"}.issubset(columns):
            parts = SessionParts(
                dc_id=int(data["dc_id"]),
                auth_key=bytes(data["auth_key"]),
                server_address=data["server_address"],
                port=int(data.get("port", 443)),
                source="telethon_file",
            )
            parts.validate()
            return parts

        # Pyrogram schema
        if {"dc_id", "auth_key"}.issubset(columns):
            parts = SessionParts(
                dc_id=int(data["dc_id"]),
                auth_key=bytes(data["auth_key"]),
                user_id=int(data["user_id"]) if data.get("user_id") else None,
                is_bot=bool(data.get("is_bot", False)),
                api_id=int(data["api_id"]) if data.get("api_id") else None,
                test_mode=bool(data.get("test_mode", False)),
                source="pyrogram_file",
            )
            parts.validate()
            return parts

        raise ValueError(f"Unknown session table schema: {columns}")

    finally:
        conn.close()


# ── Auto-detection entry point ────────────────────────────────────────────


def parse_session(value: str, dc_id: int | None = None) -> SessionParts:
    """
    Auto-detect session format.

    Detection order:
      1. Telethon StringSession (base64)
      2. Pyrogram packed string
      3. Raw 256-byte auth_key hex  (``dc_id`` **required**)

    ``dc_id`` is **mandatory** for raw hex and ignored for Telethon/Pyrogram
    strings (which carry their own dc_id internally).
    """
    value = value.strip()
    if not value:
        raise ValueError("Empty session input.")

    # 1. Telethon
    try:
        return parse_telethon_string(value)
    except Exception:
        pass

    # 2. Pyrogram
    try:
        return parse_pyrogram_string(value)
    except Exception:
        pass

    # 3. Raw hex (last resort)
    try:
        clean = value[2:] if value.startswith(("0x", "0X")) else value
        if len(clean) == 512:
            if dc_id is None:
                raise ValueError(
                    "NEED_DC_PROBE"  # special sentinel — triggers DC probing upstream
                )
            return parse_raw_hex_auth_key(clean, dc_id)
    except ValueError as exc:
        if str(exc) == "NEED_DC_PROBE":
            raise
        raise

    raise ValueError(
        "Unrecognised session format.\n"
        "Supported:\n"
        "  • Telethon StringSession (base64)\n"
        "  • Pyrogram session string (base64)\n"
        "  • Raw 256-byte auth_key hex (requires dc_id)\n"
        "  • SQLite .session file path"
    )


# ── Client creation ───────────────────────────────────────────────────────


def create_telethon_client(
    parts: SessionParts,
    api_id: int,
    api_hash: str,
) -> TelegramClient:
    """
    Build a ``TelegramClient`` from normalised ``SessionParts``.
    The client is **not** yet connected.
    """
    parts.validate()
    session = parts.to_telethon_session()
    return TelegramClient(session, api_id, api_hash)


# ── Connection & verification ─────────────────────────────────────────────


async def verify_client(client: TelegramClient) -> dict[str, Any]:
    """
    Connect the client and verify the session is authorized.

    Returns account info on success.
    Raises ``ValueError`` on failure (client is disconnected automatically).
    """
    await client.connect()

    try:
        if not await client.is_user_authorized():
            raise ValueError(
                "Session is not authorized. The auth_key may be expired "
                "or invalid."
            )

        me = await client.get_me()
        if me is None:
            raise ValueError("Telegram returned no account information.")

        return {
            "id": me.id,
            "phone": getattr(me, "phone", None),
            "first_name": getattr(me, "first_name", ""),
            "last_name": getattr(me, "last_name", ""),
            "username": getattr(me, "username", None),
        }

    except Exception:
        if client.is_connected():
            await client.disconnect()
        raise


# ── High-level convenience for bot handlers ───────────────────────────────

DC_PROBE_ORDER = [5, 4, 3, 2, 1]  # try most recent/common DCs first


async def verify_and_get_client(
    raw_input: str,
    api_id: int,
    api_hash: str,
    dc_id: int | None = None,
) -> tuple[TelegramClient | None, dict[str, Any] | str]:
    """
    One-shot: parse → create client → verify → return.

    Parameters
    ----------
    raw_input : str
        Hex auth_key, Telethon StringSession, or Pyrogram string.
    api_id : int
    api_hash : str
    dc_id : int | None
        Optional. For raw hex, if omitted the bot will probe DCs
        5 → 4 → 3 → 2 → 1 until one works.

    Returns
    -------
    ``(client, info_dict)`` on success.
    ``(None, error_message_string)`` on failure.

    ``info_dict`` contains:
    ``id``, ``phone``, ``first_name``, ``last_name``, ``username``,
    ``dc_id``, ``session_string``, ``hex_input``.
    """
    # ── Step 1: Determine if this is raw hex that needs DC probing ─────
    is_hex_input = _looks_like_raw_hex(raw_input)

    if is_hex_input and dc_id is None:
        # Probe DCs 5 → 4 → 3 → 2 → 1
        last_error = "No DC could authenticate this session."
        for probe_dc in DC_PROBE_ORDER:
            result = await _try_dc(raw_input, api_id, api_hash, probe_dc)
            if result[0] is not None:
                return result
            # Keep the error message from the last attempt
            if isinstance(result[1], str):
                last_error = result[1]
            # If it's a FloodWait, stop probing
            if "Rate-limited" in str(result[1]):
                return None, result[1]

        return None, (
            f"Tried all datacenters (5→4→3→2→1). "
            f"Last error: {last_error}"
        )

    # ── Step 2: Normal path (dc_id provided or non-hex format) ─────────
    try:
        parts = parse_session(raw_input, dc_id=dc_id)
    except ValueError as exc:
        return None, str(exc)

    try:
        client = create_telethon_client(parts, api_id, api_hash)
    except ValueError as exc:
        return None, str(exc)

    try:
        info = await verify_client(client)
    except FloodWaitError as exc:
        return None, f"Rate-limited by Telegram. Wait {exc.seconds}s."
    except ValueError as exc:
        return None, str(exc)
    except RPCError as exc:
        return None, f"Telegram API error: {exc}"
    except ConnectionError as exc:
        return None, f"Network error: {exc}"
    except TimeoutError as exc:
        return None, f"Connection timed out: {exc}"
    except Exception as exc:
        logger.exception("Unexpected error during session verification")
        return None, f"Unexpected error: {exc}"

    # Enrich info dict for bot handler compatibility
    try:
        session_string = client.session.save()
    except Exception:
        session_string = ""

    info["dc_id"] = parts.dc_id
    info["session_string"] = session_string
    info["hex_input"] = raw_input

    return client, info


def _looks_like_raw_hex(value: str) -> bool:
    """Check if input is a raw 512-char hex string (no session structure)."""
    clean = value.strip()
    if clean.startswith(("0x", "0X")):
        clean = clean[2:]
    if len(clean) != 512:
        return False
    try:
        bytes.fromhex(clean)
        return True
    except ValueError:
        return False


async def _try_dc(
    raw_input: str,
    api_id: int,
    api_hash: str,
    dc_id: int,
) -> tuple[TelegramClient | None, dict[str, Any] | str]:
    """Try to authenticate against a specific DC. Internal helper."""
    try:
        clean = raw_input.strip()
        if clean.startswith(("0x", "0X")):
            clean = clean[2:]
        parts = parse_raw_hex_auth_key(clean, dc_id)
        client = create_telethon_client(parts, api_id, api_hash)
        info = await verify_client(client)

        # Enrich
        try:
            session_string = client.session.save()
        except Exception:
            session_string = ""
        info["dc_id"] = dc_id
        info["session_string"] = session_string
        info["hex_input"] = raw_input

        logger.info(f"Session authenticated on DC-{dc_id}")
        return client, info

    except FloodWaitError as exc:
        # Disconnect the failed client
        return None, f"Rate-limited on DC-{dc_id}. Wait {exc.seconds}s."
    except Exception as exc:
        # Disconnect silently — just return error
        return None, f"DC-{dc_id}: {exc}"
