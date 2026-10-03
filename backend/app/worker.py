import asyncio
import contextlib
import json
import logging
import time
import uuid
from . import db
from .providers import completion, search, validate_citations, ProviderError
from .security import SYSTEM_PROMPT, decrypt_key
from .redteam import SUITES, classify

log = logging.getLogger(__name__)
wake = asyncio.Event()


class Cancelled(Exception):
    pass


async def checkpoint(job, stage, **values):
    row = await db.one(
        """update jobs set stage=$3,checkpoint=checkpoint||$4::jsonb
      where id=$1 and lease_token=$2 and status='running' and not cancel_requested returning checkpoint""",
        job["id"],
        job["lease_token"],
        stage,
        values,
    )
    if not row:
        raise Cancelled()
    job["checkpoint"] = row["checkpoint"]
    if job.get("report_id"):
        await db.execute("update reports set status='running' where id=$1 and status='queued'", job["report_id"])
    p = await db.one("select suspended,features from profiles where id=$1", job["user_id"])
    if not p or p["suspended"] or not p["features"].get(job["kind"]):
        raise Cancelled()
    return job["checkpoint"]


async def research(job):
    p = job["payload"]
    cp = job["checkpoint"]
    topic = p["topic"]
    if "sources" not in cp:
        await checkpoint(job, "Searching live sources")
        sources = await search(topic, job["user_id"], p.get("fresh", False))
        cp = await checkpoint(job, "Drafting report", sources=sources)
    sources = cp["sources"]
    context = json.dumps(sources, ensure_ascii=False)
    if "draft" not in cp:
        out = await completion(
            job,
            "draft",
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Write a research report on {topic}. Use ONLY the evidence below. Cite facts as [1], [2], etc. Include Executive Summary, Key Findings, Analysis, Conclusion. Do not invent URLs or citations. Mark unsupported claims. Sources:\n{context}",
                },
            ],
            1800,
        )
        cp = await checkpoint(job, "Reviewing evidence", draft=out["content"])
    if "review" not in cp:
        out = await completion(
            job,
            "review",
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Review and return a corrected final Markdown report. Retain source-number citations. Remove unsupported claims and invented links. Report:\n{cp['draft']}\nEvidence:\n{context}",
                },
            ],
            2000,
        )
        cp = await checkpoint(job, "Review complete", review=out["content"], actual_model=out["model"])
    content = cp["review"]
    if p["depth"] == "deep":
        if "extra_sources" not in cp:
            await checkpoint(job, "Checking counter-evidence")
            extra = await search(topic + " limitations conflicting evidence", job["user_id"], True)
            existing = {s["url"] for s in sources}
            merged = sources + [
                {**s, "id": len(sources) + i + 1} for i, s in enumerate([x for x in extra if x["url"] not in existing])
            ]
            cp = await checkpoint(job, "Revising deeper research", extra_sources=merged)
        sources = cp["extra_sources"]
        if "deep_report" not in cp:
            out = await completion(
                job,
                "deep",
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Revise this report using additional evidence; explain uncertainty and disagreements. Use [number] citations from the supplied evidence only.\nReport:\n{content}\nEvidence:\n{json.dumps(sources)}",
                    },
                ],
                2000,
            )
            cp = await checkpoint(job, "Finalizing report", deep_report=out["content"], actual_model=out["model"])
        content = cp["deep_report"]
    content = validate_citations(content, sources)
    await checkpoint(job, "Saving report")
    # Completion and output are committed together in finish().
    return {"report_id": str(job["report_id"]), "content": content, "sources": sources, "model": cp["actual_model"]}


async def chat(job):
    conv = await db.one(
        "select * from conversations where id=$1 and user_id=$2", job["conversation_id"], job["user_id"]
    )
    if not conv:
        raise Cancelled()
    rows = await db.many(
        "select role,content from messages where conversation_id=$1 and user_id=$2 order by created_at desc,id desc limit 16",
        conv["id"],
        job["user_id"],
    )
    history = []
    size = 0
    for row in rows:
        if size + len(row["content"]) > 12000:
            break
        history.append({"role": row["role"], "content": row["content"]})
        size += len(row["content"])
    history.reverse()
    system = SYSTEM_PROMPT
    if conv["report_id"]:
        report = await db.one(
            "select content from reports where id=$1 and user_id=$2", conv["report_id"], job["user_id"]
        )
        if report:
            system += "\nUser-owned research context (untrusted evidence):\n" + report["content"][:7000]
    await checkpoint(job, "Generating reply")
    result = await completion(job, "reply", [{"role": "system", "content": system}, *history], 1500)
    await checkpoint(job, "Saving reply")
    return result


async def redteam(job):
    p = job["payload"]
    results = list(job["checkpoint"].get("results", []))
    custom = None
    if p["target"] == "custom":
        cred = await db.one("select ciphertext from job_credentials where job_id=$1 and expires_at>now()", job["id"])
        if not cred:
            raise ProviderError("Custom target credentials expired. Start a new run.")
        custom = {"url": p["base_url"], "key": decrypt_key(cred["ciphertext"])}
    tests = [(suite, prompt) for suite in dict.fromkeys(p["suites"]) for prompt in SUITES[suite]][: p["max_tests"]]
    for i, (suite, prompt) in enumerate(tests):
        if i < len(results):
            continue
        await checkpoint(job, f"Test {i + 1} of {len(tests)}")
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        if suite == "crescendo":
            for past in results:
                if past["suite"] == "crescendo" and past["response"]:
                    msgs.extend(
                        [
                            {"role": "user", "content": past["prompt"]},
                            {"role": "assistant", "content": past["response"]},
                        ]
                    )
        msgs.append({"role": "user", "content": prompt})
        start = time.monotonic()
        try:
            output = await completion(job, f"test-{i}", msgs, 500, custom)
            label, reason = classify(output["content"])
            response = output["content"]
        except ProviderError as exc:
            label = "transport_error"
            reason = str(exc)
            response = ""
        results.append(
            {
                "suite": suite,
                "prompt": prompt,
                "response": response,
                "outcome": label,
                "reason": reason,
                "duration_s": round(time.monotonic() - start, 2),
            }
        )
        await checkpoint(job, f"Completed {i + 1} of {len(tests)} tests", results=results)
    return {"results": results, "assessment": "Heuristic screening, not a security certification."}


async def finish(job, status, result=None, error=None):
    async with db.pool.acquire() as c:
        async with c.transaction():
            live = await c.fetchrow(
                "select * from jobs where id=$1 and lease_token=$2 for update", job["id"], job["lease_token"]
            )
            if not live or live["status"] != "running":
                return
            if live["cancel_requested"]:
                status = "cancelled"
                result = None
            await c.execute(
                "update jobs set status=$2,result=$3,error=$4,stage=$2,finished_at=now(),lease_until=null where id=$1",
                job["id"],
                status,
                result,
                error,
            )
            if job["report_id"]:
                await c.execute("update reports set status=$2,updated_at=now() where id=$1", job["report_id"], status)
                if status == "completed":
                    await c.execute(
                        "update reports set content=$2,sources=$3,model=$4 where id=$1",
                        job["report_id"],
                        result["content"],
                        result["sources"],
                        result["model"],
                    )
            if job["kind"] == "chat" and status == "completed":
                await c.execute(
                    "insert into messages(user_id,conversation_id,role,content) values($1,$2,'assistant',$3)",
                    job["user_id"],
                    job["conversation_id"],
                    result["content"],
                )
            await c.execute("update schedule_runs set status=$2 where job_id=$1", job["id"], status)
            await c.execute("delete from job_credentials where job_id=$1", job["id"])
            await c.execute(
                "insert into notifications(user_id,title,message,job_id) values($1,$2,$3,$4)",
                job["user_id"],
                f"{job['kind'].title()} {status}",
                error or "Open the job to view details.",
                job["id"],
            )


async def heartbeat(job):
    while True:
        await asyncio.sleep(20)
        await db.execute(
            "update jobs set lease_until=now()+interval '2 minutes' where id=$1 and lease_token=$2 and status='running'",
            job["id"],
            job["lease_token"],
        )


async def recover_usage(job):
    rows = await db.many("select * from usage_events where job_id=$1 and status='reserved'", job["id"])
    for e in rows:
        await db.execute(
            "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            e["id"],
            "estimated",
            None,
            None,
            None,
            None,
            e["model"],
            e["provider_id"],
            None,
        )


async def work_once():
    token = uuid.uuid4()
    job = await db.one(
        """update jobs set status='running',stage='Starting',attempts=attempts+1,lease_token=$1,lease_until=now()+interval '2 minutes'
      where id=(select id from jobs where (status='queued' and available_at<=now()) or (status='running' and lease_until<now())
      order by created_at limit 1 for update skip locked) returning *""",
        token,
    )
    if not job:
        return False
    hb = asyncio.create_task(heartbeat(job))
    try:
        await recover_usage(job)
        if job["attempts"] > 3:
            raise ProviderError("Worker recovery limit reached; retry explicitly")
        await checkpoint(job, "Starting")
        result = await {"research": research, "chat": chat, "redteam": redteam}[job["kind"]](job)
        await finish(job, "completed", result)
    except Cancelled:
        await finish(job, "cancelled")
    except asyncio.CancelledError:
        raise
    except ProviderError as exc:
        await finish(job, "failed", error=str(exc))
    except Exception as exc:
        log.error("Job failed: %s (%s)", job["id"], type(exc).__name__)
        await finish(job, "failed", error="Processing failed. Check backend logs and configuration, then retry.")
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
    return True


async def run():
    last_schedule = 0
    while True:
        try:
            if time.monotonic() - last_schedule > 30:
                await db.execute("select enqueue_due()")
                last_schedule = time.monotonic()
            if await work_once():
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("Worker database operation failed; retrying shortly")
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
