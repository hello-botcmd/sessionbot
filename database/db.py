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
        """Initialize MongoDB connection."""
        self.client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        # Create indexes
        await self.db.accounts.create_index("user_hex_id", unique=True, sparse=True)
        await self.db.sudo_users.create_index("user_id", unique=True)
        await self.db.mails.create_index("owner_id", unique=True)

    def get_db(self):
        return self.db

    async def close(self):
        if self.client:
            self.client.close()

db = Database()
