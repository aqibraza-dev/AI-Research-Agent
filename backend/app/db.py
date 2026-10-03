import json
import asyncpg
from .config import settings

pool = None


async def connect():
    global pool

    async def init(conn):
        for name in ("json", "jsonb"):
            await conn.set_type_codec(name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")

    pool = await asyncpg.create_pool(
        settings().database_url,
        min_size=1,
        max_size=4,
        statement_cache_size=0,
        command_timeout=30,
        ssl="require" if settings().database_ssl else False,
        init=init,
    )


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
