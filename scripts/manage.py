"""Operator-only database management. Run with backend/.venv/bin/python."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    from dotenv import load_dotenv

    load_dotenv(ROOT / "backend/.env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL in the environment or backend/.env")
    conn = await asyncpg.connect(
        url,
        statement_cache_size=0,
        ssl="require" if os.getenv("DATABASE_SSL", "true").lower() == "true" else False,
    )
    try:
        if args.command == "migrate":
            await conn.execute(
                "create table if not exists public.schema_migrations(name text primary key,applied_at timestamptz default now())"
            )
            await conn.execute(
                "revoke all on public.schema_migrations from anon,authenticated"
            )
            for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
                async with conn.transaction():
                    if await conn.fetchval(
                        "select exists(select 1 from public.schema_migrations where name=$1)",
                        path.name,
                    ):
                        continue
                    await conn.execute(path.read_text())
                    await conn.execute(
                        "insert into public.schema_migrations(name) values($1)",
                        path.name,
                    )
                print("Applied", path.name)
        elif args.command == "promote-admin":
            async with conn.transaction():
                row = await conn.fetchrow(
                    "update public.profiles set role='admin' where lower(email)=lower($1) returning id,email",
                    args.email,
                )
                if not row:
                    raise SystemExit("No matching user. Sign up first.")
                await conn.execute(
                    "insert into public.audit_events(action,target_id,after_value) values('operator_promote_admin',$1,$2::jsonb)",
                    str(row["id"]),
                    json.dumps({"email": row["email"], "role": "admin"}),
                )
                print("Administrator promoted:", row["email"])
        elif args.command == "reconcile-usage":
            # Only replace a conservative estimate with independently verified values.
            async with conn.transaction():
                e = await conn.fetchrow(
                    "select * from public.usage_events where id=$1::uuid and status='estimated' for update",
                    args.event_id,
                )
                if not e:
                    raise SystemExit("Estimated event not found")
                actual = args.input_tokens + args.output_tokens
                await conn.execute(
                    "update public.daily_usage set charged=charged-$3+$4 where user_id=$1 and day=$2",
                    e["user_id"],
                    e["day"],
                    e["charged_tokens"],
                    0 if e["external"] else actual,
                )
                await conn.execute(
                    "update public.usage_events set status='confirmed',input_tokens=$2,output_tokens=$3,charged_tokens=$4,cost_usd=$5 where id=$1",
                    e["id"],
                    args.input_tokens,
                    args.output_tokens,
                    0 if e["external"] else actual,
                    args.cost,
                )
                await conn.execute(
                    "insert into public.audit_events(action,target_id,after_value) values('operator_reconcile_usage',$1,$2::jsonb)",
                    str(e["id"]),
                    json.dumps(
                        {
                            "input_tokens": args.input_tokens,
                            "output_tokens": args.output_tokens,
                            "cost": args.cost,
                        }
                    ),
                )
                print("Usage reconciled")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    admin = sub.add_parser("promote-admin")
    admin.add_argument("email")
    rec = sub.add_parser("reconcile-usage")
    rec.add_argument("event_id")
    rec.add_argument("input_tokens", type=int)
    rec.add_argument("output_tokens", type=int)
    rec.add_argument("--cost", type=float, default=0)
    args = parser.parse_args()
    if args.command == "reconcile-usage" and (
        args.input_tokens < 0 or args.output_tokens < 0 or args.cost < 0
    ):
        parser.error("Usage and cost must be nonnegative")
    asyncio.run(main(args))
