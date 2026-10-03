import asyncio
import time
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from . import db, cache
from .config import settings, MODELS
from .security import custom_completion, TargetError


class ProviderError(Exception):
    pass


def conservative_tokens(messages, max_tokens):
    # A byte-based upper bound is deliberately more conservative than chars/4.
    return sum(len(m["content"].encode("utf-8")) + 32 for m in messages) + max_tokens + 128


async def completion(job, step, messages, max_tokens=1500, custom=None):
    prior = await db.one("select * from usage_events where job_id=$1 and step=$2", job["id"], step)
    if prior:
        if prior["response"]:
            return prior["response"]
        raise ProviderError("A previous provider call has an uncertain outcome. Usage is retained; retry explicitly.")
    model = job["payload"].get("model", MODELS[0])
    if not custom and model not in MODELS:
        raise ProviderError("Model is not permitted")
    if not custom and not settings().open_router_free_api_key:
        raise ProviderError("OpenRouter is not configured")
    amount = conservative_tokens(messages, max_tokens)
    try:
        e = await db.one(
            "select reserve_tokens($1,$2,$3,$4,$5,$6,$7) as id",
            job["user_id"],
            job["id"],
            step,
            job["kind"],
            model,
            amount,
            bool(custom),
        )
    except Exception as exc:
        if "QUOTA_EXCEEDED" in str(exc):
            raise ProviderError(
                "Daily token allowance is insufficient for this call. Reduce depth or wait for UTC reset."
            )
        if "FEATURE_DISABLED" in str(exc):
            raise ProviderError("Account or feature is disabled")
        raise
    start = time.monotonic()
    data = None
    try:
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        if custom:
            data = await custom_completion(custom["url"], custom["key"], payload)
        else:
            if not settings().open_router_free_api_key:
                raise ProviderError("OpenRouter is not configured")
            # Explicitly prohibit priced provider routing and paid plugins.
            payload["provider"] = {"max_price": {"prompt": 0, "completion": 0}}
            async with httpx.AsyncClient(timeout=90) as c:
                r = await c.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": "Bearer " + settings().open_router_free_api_key},
                )
            if r.status_code in (400, 401, 402, 403, 404, 422, 429):
                await settle(e["id"], "rejected", None, None, None, None, model, None, start)
                if r.status_code == 429:
                    raise ProviderError("Provider rate limit reached. Retry later; no paid fallback was used.")
                raise ProviderError(
                    f"Provider rejected the request (HTTP {r.status_code}). Check model availability and configuration."
                )
            r.raise_for_status()
            data = r.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty response")
        secret = custom["key"] if custom else settings().open_router_free_api_key
        if secret:
            content = content.replace(secret, "[REDACTED CREDENTIAL]")
        u = data.get("usage") or {}
        inp = u.get("prompt_tokens")
        out = u.get("completion_tokens")
        confirmed = isinstance(inp, int) and isinstance(out, int) and inp >= 0 and out >= 0
        response = {"content": content, "model": data.get("model", model)}
        cost = u.get("cost") if custom else (u.get("cost", 0) if confirmed else None)
        await settle(
            e["id"],
            "confirmed" if confirmed else "estimated",
            inp if confirmed else None,
            out if confirmed else None,
            cost,
            response,
            response["model"],
            data.get("id"),
            start,
        )
        return response
    except BaseException as exc:
        await settle(
            e["id"],
            "estimated",
            None,
            None,
            None,
            None,
            model,
            data.get("id") if isinstance(data, dict) else None,
            start,
        )
        if isinstance(exc, asyncio.CancelledError):
            raise
        if isinstance(exc, ProviderError):
            raise
        if isinstance(exc, TargetError):
            raise ProviderError(str(exc))
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ProviderError(
            "Provider call interrupted or failed. Its outcome may be uncertain; usage is retained."
        ) from None


async def settle(eid, status, inp, out, cost, response, model, pid, start):
    await db.execute(
        "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        eid,
        status,
        inp,
        out,
        cost,
        response,
        model,
        pid,
        int((time.monotonic() - start) * 1000),
    )


async def search(topic, uid, fresh=False):
    import hashlib

    key = "search:" + str(uid) + ":" + hashlib.sha256(topic.encode()).hexdigest()
    if not fresh:
        cached = await cache.get(key)
        if cached:
            return cached
    if not settings().tavily_api_key:
        raise ProviderError("Live search requires TAVILY_API_KEY. No model-only report was generated.")
    async with db.pool.acquire() as c:
        async with c.transaction():
            limit = await c.fetchval("select search_monthly_limit from app_settings where id for update")
            row = await c.fetchrow("""insert into search_usage(month,credits) values(date_trunc('month',now() at time zone 'UTC')::date,1)
                on conflict(month) do update set credits=search_usage.credits+1 returning credits""")
            if row["credits"] > limit:
                raise ProviderError("Monthly search allowance exhausted")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings().tavily_api_key,
                    "query": topic,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
        r.raise_for_status()
        raw = r.json().get("results", [])
        results = []
        for item in raw:
            url = item.get("url", "")
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
                continue
            results.append(
                {
                    "id": len(results) + 1,
                    "title": item.get("title", "Source")[:300],
                    "url": url,
                    "excerpt": item.get("content", "")[:1000],
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        if not results:
            raise ProviderError("Search returned no usable sources. Try a more specific topic.")
        await cache.put(key, results, 600)
        return results
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("Live search failed. No model-only report was generated.") from None


def validate_citations(content, sources):
    allowed = {str(s["id"]) for s in sources}
    bad = set(re.findall(r"\[(\d+)\]", content)) - allowed
    if bad:
        raise ProviderError("Report contained invalid source references; retry research")
    if sources and not re.search(r"\[\d+\]", content):
        raise ProviderError("Report did not cite retrieved sources; retry research")
    # Model-written URLs are not accepted as evidence; attach canonical source list.
    urls = re.findall(r"https?://[^\s)<>]+", content)
    if any(url.rstrip(".,") not in {s["url"] for s in sources} for url in urls):
        raise ProviderError("Report included a link not present in retrieved sources")
    return content + "\n\n## Sources\n" + "\n".join(f"[{s['id']}] {s['title']} — {s['url']}" for s in sources)
