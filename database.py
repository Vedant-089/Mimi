import asyncpg
import asyncio
import os 
from dotenv import load_dotenv

load_dotenv()

class Database:
    def __init__(self, dsn):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        """Establish a connection pool with PostgreSQL."""
        if not self.pool:
            self.pool = await asyncpg.create_pool(
    self.dsn,
    min_size=1,
    max_size=5,
    max_inactive_connection_lifetime=30.0,
    command_timeout=10.0,
    statement_cache_size=0
)

    async def close(self):
        """Close the database connection pool."""
        if self.pool:
            await self.pool.close()

    async def execute(self, query, *args):
        """Execute a query that doesn't return data (INSERT, UPDATE, DELETE)."""
        async with self.pool.acquire() as conn:
            await conn.execute(query, *args)

    async def fetch(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)


# Create a single global database instance
db = Database(os.getenv("DATABASE_URL"))

# Function to initialize the database at bot startup
async def setup_database():
    await db.connect()

# Function to get the database pool (so other files can use it)
async def get_database_pool():
    """Returns the database pool from the Database instance."""
    if not db.pool:
        await db.connect()
    return db.pool
