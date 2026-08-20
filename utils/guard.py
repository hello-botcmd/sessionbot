"""In-memory guard-mode manager.

Guards are kept in ``application.bot_data`` so they survive across updates and
handlers (unlike ``user_data``, which is tied to a single user/chat context).
"""
import asyncio
import logging
from datetime import datetime, timezone

from telethon.errors import FloodWaitError

from config import GUARD_INTERVAL
from utils.helpers import get_devices, terminate_device

logger = logging.getLogger(__name__)

_BOT_DATA_KEY = "guard_manager_entries"


class GuardManager:
    def __init__(self, application):
        self.application = application
        self.interval = GUARD_INTERVAL

    # -- storage -------------------------------------------------------------
    def _entries(self) -> dict:
        return self.application.bot_data.setdefault(_BOT_DATA_KEY, {})

    @staticmethod
    def key(user_id: int, account_uid: int) -> str:
        return f"{user_id}:{account_uid}"

    # -- lifecycle -----------------------------------------------------------
    def add(self, user_id: int, account_uid: int, client, chat_id: int,
            task: asyncio.Task) -> str:
        k = self.key(user_id, account_uid)
        self._entries()[k] = {
            "user_id": user_id,
            "account_uid": account_uid,
            "client": client,
            "chat_id": chat_id,
            "active": True,
            "allow_until": None,
            "task": task,
            "notified": set(),
            "started_at": datetime.now(timezone.utc),
        }
        logger.info(f"Guard started: {k}")
        return k

    def get(self, key: str):
        return self._entries().get(key)

    def list_for_user(self, user_id: int) -> list:
        return [g for g in self._entries().values() if g["user_id"] == user_id]

    def allow_login(self, user_id: int, account_uid: int, until: datetime) -> bool:
        """Temporarily allow logins (skips termination) until ``until``."""
        found = False
        for g in self._entries().values():
            if g["user_id"] == user_id and g["account_uid"] == account_uid:
                g["allow_until"] = until
                found = True
        return found

    async def stop(self, key: str, notify: bool = True) -> bool:
        """Stop a guard: cancel loop, disconnect client, remove entry."""
        entry = self._entries().pop(key, None)
        if not entry:
            return False
        entry["active"] = False

        task = entry.get("task")
        current = asyncio.current_task()
        if task and task is not current and not task.done():
            task.cancel()

        client = entry.get("client")
        if client and client.is_connected():
            try:
                await client.disconnect()
            except Exception:
                pass

        if notify:
            try:
                await self.application.bot.send_message(
                    entry["chat_id"], "🛡️ **Guard mode stopped.**\n\nAll monitoring ended."
                )
            except Exception as e:
                logger.warning(f"Guard stop notify failed: {e}")
        logger.info(f"Guard stopped: {key}")
        return True

    async def stop_for_user(self, user_id: int, account_uid: int | None = None,
                            notify: bool = False):
        stopped = 0
        for k in list(self._entries().keys()):
            entry = self._entries().get(k)
            if not entry or entry["user_id"] != user_id:
                continue
            if account_uid is not None and entry["account_uid"] != account_uid:
                continue
            if await self.stop(k, notify=notify):
                stopped += 1
        return stopped

    async def stop_all(self):
        for k in list(self._entries().keys()):
            await self.stop(k, notify=False)


# ═══════════════════════════════════════════════════════════════════════════
#  Guard background loop + helpers (shared by the Guard handler and the
#  Device-Dashboard on/off toggle)
# ═══════════════════════════════════════════════════════════════════════════
async def notify_user(application, entry: dict, text: str):
    try:
        await application.bot.send_message(
            entry["chat_id"], text, parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning("Guard notify failed: %s", e)


async def guard_loop(application, key: str):
    """Check every ``GUARD_INTERVAL`` seconds and kill any new session."""
    manager = GuardManager(application)
    logger.info("Guard loop started: %s", key)

    try:
        while True:
            entry = manager.get(key)
            if not entry or not entry["active"]:
                return

            client = entry["client"]

            # Reconnect / verify authorization
            try:
                if not client.is_connected():
                    await client.connect()
                if not await client.is_user_authorized():
                    await notify_user(application, entry, "⚠️ **Guard stopped** — session expired.")
                    await manager.stop(key)
                    return
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Guard %s reconnect error: %s", key, e)
                await asyncio.sleep(3)
                continue

            # Fetch devices (respect flood waits)
            try:
                devices = await get_devices(client, raise_errors=True)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Guard %s device error: %s", key, e)
                await asyncio.sleep(3)
                continue

            allow_until = entry.get("allow_until")
            for dev in devices:
                if dev.get("current"):
                    continue
                # Honour a temporary "allow login" window
                if allow_until and datetime.now(timezone.utc) < allow_until:
                    continue
                if await terminate_device(client, dev["hash"]):
                    await notify_terminated(application, entry, dev)

            await asyncio.sleep(manager.interval)

    except asyncio.CancelledError:
        logger.info("Guard loop cancelled: %s", key)
    except Exception as e:
        logger.error("Guard loop error: %s", e)


async def notify_terminated(application, entry: dict, dev: dict):
    """Notify once per terminated device hash (dedupe)."""
    h = dev.get("hash")
    if h in entry["notified"]:
        return
    entry["notified"].add(h)

    text = (
        "🚨 **Unauthorized Login Terminated!** 🚨\n\n"
        "📱 Session killed immediately:\n"
        f"├─ Model: {dev.get('device_model', 'Unknown')}\n"
        f"├─ Platform: {dev.get('platform', 'Unknown')}\n"
        f"├─ IP: {dev.get('ip') or 'Unknown'}\n"
        f"├─ Region: {dev.get('region') or '?'} {dev.get('country') or ''}\n"
        f"├─ App: {dev.get('app_name', '')}\n"
        f"└─ Action: ✅ **Terminated**"
    )
    await notify_user(application, entry, text)


async def start_guard(application, user_id: int, account_uid: int,
                      client, chat_id: int) -> str:
    """Start (or restart) a guard loop for one account. Returns the guard key."""
    manager = GuardManager(application)
    key = manager.key(user_id, account_uid)
    if manager.get(key):
        await manager.stop(key, notify=False)
    task = asyncio.create_task(guard_loop(application, key))
    manager.add(user_id, account_uid, client, chat_id, task)
    return key
