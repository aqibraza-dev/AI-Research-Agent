import errno
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import db


DIRECT_URL = "postgresql://postgres:private-password@db.project.supabase.co:5432/postgres"
POOLER_URL = "postgresql://postgres.project:private-password@aws-0-example.pooler.supabase.com:6543/postgres"


def configure(monkeypatch, url, *, result=None, error=None):
    monkeypatch.setattr(db, "pool", None)
    monkeypatch.setattr(db, "settings", lambda: SimpleNamespace(database_url=url, database_ssl=True))
    create_pool = AsyncMock(return_value=result, side_effect=error)
    monkeypatch.setattr(db.asyncpg, "create_pool", create_pool)
    return create_pool


async def test_unreachable_direct_endpoint_has_safe_actionable_error(monkeypatch):
    error = OSError(errno.ENETUNREACH, "Network is unreachable")
    configure(monkeypatch, DIRECT_URL, error=error)
    with pytest.raises(RuntimeError) as caught:
        await db.connect()
    message = str(caught.value)
    assert "Transaction pooler" in message
    assert "DATABASE_URL" in message
    assert "6543" in message
    assert "private-password" not in message
    assert DIRECT_URL not in message
    assert caught.value.__suppress_context__
    assert db.pool is None


@pytest.mark.parametrize("url", [DIRECT_URL, POOLER_URL])
async def test_reachable_endpoint_connects_with_pooler_safe_options(monkeypatch, url):
    pool = object()
    create_pool = configure(monkeypatch, url, result=pool)
    await db.connect()
    assert db.pool is pool
    assert create_pool.await_args.args == (url,)
    assert create_pool.await_args.kwargs["statement_cache_size"] == 0
    assert create_pool.await_args.kwargs["ssl"] == "require"


@pytest.mark.parametrize(
    "url,error",
    [
        (DIRECT_URL, OSError(errno.ECONNREFUSED, "Connection refused")),
        (POOLER_URL, OSError(errno.ENETUNREACH, "Network is unreachable")),
        (DIRECT_URL, db.asyncpg.InvalidPasswordError("Invalid password")),
    ],
)
async def test_other_failures_are_not_misdiagnosed(monkeypatch, url, error):
    configure(monkeypatch, url, error=error)
    with pytest.raises(type(error)) as caught:
        await db.connect()
    assert caught.value is error
