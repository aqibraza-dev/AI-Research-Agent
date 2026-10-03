import json
import logging
import redis.asyncio as redis
from fastapi import HTTPException
from .config import settings
from . import db

client = None
logger = logging.getLogger(__name__)


def connection():
    global client
    if client is None and settings().redis_url:
        client = redis.from_url(settings().redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    return client


async def get(key):
    try:
        c = connection()
        value = await c.get(key) if c else None
        return json.loads(value) if value else None
    except Exception:
        return None


async def put(key, value, ttl=300):
    try:
        c = connection()
        if c:
            await c.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


async def throttle(uid):
    c = connection()
    try:
        if not c:
            raise RuntimeError("Redis unavailable")
        count = await c.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
            1,
            f"rate:{uid}",
        )
    except Exception:
        r = await db.one(
            """insert into request_limits(key,window_start,count) values($1,now(),1)
          on conflict(key) do update set count=case when request_limits.window_start<now()-interval '1 minute' then 1 else request_limits.count+1 end,
          window_start=case when request_limits.window_start<now()-interval '1 minute' then now() else request_limits.window_start end returning count""",
            str(uid),
        )
        count = r["count"]
    if count > 20:
        raise HTTPException(429, "Too many requests. Try again in one minute.")
