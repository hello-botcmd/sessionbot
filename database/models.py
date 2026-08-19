from datetime import datetime, timezone
from bson import ObjectId
from database.db import db


# ── Accounts ──────────────────────────────────────────────────────────────────
async def save_account(owner_id: int, hex_key: str, phone: str, name: str,
                       user_id: int, dc_id: int, session_string: str):
    """Save a managed account to MongoDB."""
    collection = db.get_db()["accounts"]
    existing = await collection.find_one({"owner_id": owner_id, "user_id": user_id})
    data = {
        "owner_id": owner_id,
        "hex_key": hex_key,
        "phone": phone,
        "name": name,
        "user_id": user_id,
        "dc_id": dc_id,
        "session_string": session_string,
        "guard_active": False,
        "guard_allow_until": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    if existing:
        await collection.update_one({"_id": existing["_id"]}, {"$set": data})
        return existing["_id"]
    result = await collection.insert_one(data)
    return result.inserted_id


async def get_accounts_by_owner(owner_id: int):
    """Get all accounts for a given owner."""
    collection = db.get_db()["accounts"]
    cursor = collection.find({"owner_id": owner_id}).sort("created_at", -1)
    return await cursor.to_list(length=None)


async def get_account_by_user_id(owner_id: int, user_id: int):
    """Get a specific account by Telegram user_id."""
    collection = db.get_db()["accounts"]
    return await collection.find_one({"owner_id": owner_id, "user_id": user_id})


async def get_account_by_id(account_id: str):
    """Get an account by its MongoDB _id."""
    collection = db.get_db()["accounts"]
    return await collection.find_one({"_id": ObjectId(account_id)})


async def update_account(account_id: str, update_data: dict):
    """Update an account document."""
    collection = db.get_db()["accounts"]
    update_data["updated_at"] = datetime.now(timezone.utc)
    return await collection.update_one(
        {"_id": ObjectId(account_id)}, {"$set": update_data}
    )


async def delete_account(account_id: str):
    """Delete an account."""
    collection = db.get_db()["accounts"]
    return await collection.delete_one({"_id": ObjectId(account_id)})


# ── Sudo Users ───────────────────────────────────────────────────────────────
async def add_sudo_user(sudo_id: int, added_by: int):
    """Add a sudo user."""
    collection = db.get_db()["sudo_users"]
    existing = await collection.find_one({"user_id": sudo_id})
    if existing:
        return False
    await collection.insert_one({
        "user_id": sudo_id,
        "added_by": added_by,
        "added_at": datetime.now(timezone.utc),
    })
    return True


async def remove_sudo_user(sudo_id: int):
    """Remove a sudo user."""
    collection = db.get_db()["sudo_users"]
    result = await collection.delete_one({"user_id": sudo_id})
    return result.deleted_count > 0


async def get_all_sudo_users():
    """Get list of all sudo users."""
    collection = db.get_db()["sudo_users"]
    return await collection.find().to_list(length=None)


async def is_sudo_user(user_id: int):
    """Check if a user is a sudo user (includes owners)."""
    if user_id in __import__("config").OWNER_IDS:
        return True
    collection = db.get_db()["sudo_users"]
    result = await collection.find_one({"user_id": user_id})
    return result is not None


# ── Mails ────────────────────────────────────────────────────────────────────
async def save_mail(owner_id: int, email: str, app_password: str):
    """Save an email configuration for a user (for change-mail feature)."""
    collection = db.get_db()["mails"]
    await collection.update_one(
        {"owner_id": owner_id},
        {"$set": {
            "owner_id": owner_id,
            "email": email,
            "app_password": app_password,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


async def get_mail(owner_id: int):
    """Get saved email for a user."""
    collection = db.get_db()["mails"]
    return await collection.find_one({"owner_id": owner_id})


async def remove_mail(owner_id: int):
    """Remove saved email for a user."""
    collection = db.get_db()["mails"]
    return await collection.delete_one({"owner_id": owner_id})
