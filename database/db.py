from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI, DB_NAME


class Database:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.client = None
            cls._instance.db = None
        return cls._instance

    async def connect(self):
        """Initialize MongoDB connection and create indexes."""
        self.client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DB_NAME]

        # Unique account per (owner, telegram user_id)
        await self.db.accounts.create_index(
            [("owner_id", 1), ("user_id", 1)], unique=True
        )
        await self.db.sudo_users.create_index("user_id", unique=True)
        await self.db.mails.create_index("owner_id", unique=True)

    def get_db(self):
        return self.db

    async def close(self):
        if self.client:
            self.client.close()


db = Database()
