import errno
import json
from urllib.parse import urlsplit
import asyncpg
from .config import settings

pool = None


async def connect():
    global pool

    async def init(conn):
        for name in ("json", "jsonb"):
            await conn.set_type_codec(name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    config = settings()
    try:
        pool = await asyncpg.create_pool(
            config.database_url,
            min_size=1,
            max_size=4,
            statement_cache_size=0,
            command_timeout=30,
            ssl="require" if config.database_ssl else False,
            init=init,
        )
    except OSError as exc:
        host = urlsplit(config.database_url).hostname or ""
        if exc.errno == errno.ENETUNREACH and host.startswith("db.") and host.endswith(".supabase.co"):
            raise RuntimeError(
                "Cannot reach the Supabase direct database endpoint. It normally requires IPv6. "
                "For Render, copy the Transaction pooler URI from Supabase > Connect into DATABASE_URL "
                "(shared pooler host, project-qualified username, port 6543). "
                "Keep DATABASE_SSL=true, URL-encode the database password, and redeploy. "
                "Changing only the port on the direct endpoint will not fix this."
            ) from None
        raise



async def close():
    if pool:
        await pool.close()


async def one(sql, *args):
    async with pool.acquire() as c:
        row = await c.fetchrow(sql, *args)
        return dict(row) if row else None


async def many(sql, *args):
    async with pool.acquire() as c:
        return [dict(r) for r in await c.fetch(sql, *args)]


async def execute(sql, *args):
    async with pool.acquire() as c:
        return await c.execute(sql, *args)
