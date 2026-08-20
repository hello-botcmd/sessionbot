import logging

from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI, DB_NAME

logger = logging.getLogger(__name__)


class Database:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.client = None
            cls._instance.db = None
        return cls._instance

    async def connect(self):
        """Initialize MongoDB connection and (re)create correct indexes.

        Self-heals stale indexes left over from older bot versions — e.g. a
        unique ``hex_1`` index on ``accounts`` that no longer matches this
        schema and caused E11000 duplicate-key errors.
        """
        self.client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DB_NAME]

        # Drop every non-_id index on our collections, then recreate the ones
        # this schema actually needs. Dropping an index never deletes data.
        for coll_name in ("accounts", "sudo_users", "mails"):
            coll = self.db[coll_name]
            try:
                indexes = await coll.index_information()
                for name in list(indexes.keys()):
                    if name == "_id_":
                        continue
                    try:
                        await coll.drop_index(name)
                        logger.info("Dropped stale index %s on %s", name, coll_name)
                    except Exception as e:
                        logger.warning("Could not drop index %s on %s: %s",
                                       name, coll_name, e)
            except Exception as e:
                logger.warning("Could not inspect indexes on %s: %s", coll_name, e)

        # Recreate the indexes we want. Kept NON-unique so existing data with
        # duplicates can never crash startup; the app code already de-duplicates
        # via find_one + upsert.
        await self.db.accounts.create_index(
            [("owner_id", 1), ("user_id", 1)], unique=False
        )
        await self.db.sudo_users.create_index("user_id", unique=False)
        await self.db.mails.create_index("owner_id", unique=False)

    def get_db(self):
        return self.db

    async def close(self):
        if self.client:
            self.client.close()


db = Database()
