import os
import uuid
from pathlib import Path
import asyncpg
import pytest
import pytest_asyncio
from app import db, cache
from app.config import settings
from app.main import app
from app.auth import current_user


@pytest_asyncio.fixture
async def database(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable local PostgreSQL database")
    # This test fixture resets schemas. Deliberately disallow remote databases.
    if not any(x in url for x in ("@127.0.0.1:", "@localhost:")):
        raise RuntimeError("Tests require a local disposable database")
    conn = await asyncpg.connect(url)
    await conn.execute(
        "drop schema if exists public cascade; drop schema if exists auth cascade; create schema public; create schema auth;"
    )
    await conn.execute("""do $$ begin create role anon; exception when duplicate_object then null; end $$;
      do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
      create table auth.users(id uuid primary key,email text,raw_user_meta_data jsonb default '{}');
      create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
      grant usage on schema public,auth to anon,authenticated;
      grant execute on function auth.uid() to authenticated;""")
    for p in sorted((Path(__file__).parents[2] / "supabase/migrations").glob("*.sql")):
        await conn.execute(p.read_text())
    await conn.close()
    monkeypatch.setattr(settings(), "database_url", url)
    monkeypatch.setattr(settings(), "database_ssl", False)
    monkeypatch.setattr(settings(), "worker_enabled", False)
    monkeypatch.setattr(settings(), "redis_url", "")
    cache.client = None
    await db.connect()
    ids = [uuid.uuid4(), uuid.uuid4()]
    for i, uid in enumerate(ids):
        await db.execute(
            "insert into auth.users(id,email,raw_user_meta_data) values($1,$2,$3)",
            uid,
            f"user{i}@example.test",
            {"display_name": f"User {i}"},
        )
    yield ids
    app.dependency_overrides.clear()
    await db.close()


@pytest_asyncio.fixture
async def client(database):
    import httpx

    user = await db.one("select * from profiles where id=$1", database[0])
    app.dependency_overrides[current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
