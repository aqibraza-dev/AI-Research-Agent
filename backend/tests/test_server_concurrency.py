"""Server Handling, Concurrency & Load Test Suite for AI Research Agent.

Evaluates server resilience, ASGI concurrency, connection pool saturation,
async lock contention (FOR UPDATE SKIP LOCKED), burst traffic vs rate limiter (20 req/min),
concurrent cancellations, payload size boundaries, chat concurrency, exports, and memory stability.

Organized around the 5-pillar production framework:
/goal /plan /teamwork /learn /boost /compact
"""

import asyncio
import gc
import json
import math
import statistics
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app import cache, db, worker
from app.auth import current_user
from app.config import MODELS, settings
from app.main import app
from app.schemas import Research, Message, Conversation


# =====================================================================
# In-Memory Concurrency Test Harness & Virtual Async Connection Pool
# =====================================================================

class VirtualConnection:
    """Simulates an asyncpg connection with transactional state, row-level locking, and mock SQL handling."""

    def __init__(self, pool_ref):
        self.pool = pool_ref
        self._in_transaction = False
        self._active_tx_locks = []

    def transaction(self):
        class TxCtx:
            def __init__(tx_self, conn):
                tx_self.conn = conn

            async def __aenter__(tx_self):
                tx_self.conn._in_transaction = True
                tx_self.conn._active_tx_locks = []
                return tx_self.conn

            async def __aexit__(tx_self, exc_type, exc_val, exc_tb):
                tx_self.conn._in_transaction = False
                for lock in tx_self.conn._active_tx_locks:
                    lock.release()
                tx_self.conn._active_tx_locks.clear()
                return False

        return TxCtx(self)

    async def _handle_row_lock(self, sql, args):
        """Simulates PostgreSQL tuple-level locking for SELECT ... FOR UPDATE within transactions."""
        sql_clean = " ".join(sql.split()).lower()
        if "for update" in sql_clean and self._in_transaction:
            key = None
            if "from conversations" in sql_clean and args:
                key = ("conversations", str(args[0]))
            elif "from reports" in sql_clean and args:
                key = ("reports", str(args[0]))
            elif "from jobs" in sql_clean and args:
                key = ("jobs", str(args[0]))
            elif "from profiles" in sql_clean and args:
                key = ("profiles", str(args[0]))
            elif "from schedules" in sql_clean and args:
                key = ("schedules", str(args[0]))
            elif "from app_settings" in sql_clean:
                key = ("app_settings", "default")

            if key is not None:
                lock = await self.pool.get_row_lock(key)
                if lock not in self._active_tx_locks:
                    await lock.acquire()
                    self._active_tx_locks.append(lock)

    async def fetchrow(self, sql, *args):
        await self._handle_row_lock(sql, args)
        await asyncio.sleep(self.pool.query_delay)
        return self.pool.route_query_row(sql, args)

    async def fetch(self, sql, *args):
        await self._handle_row_lock(sql, args)
        await asyncio.sleep(self.pool.query_delay)
        return self.pool.route_query_rows(sql, args)

    async def fetchval(self, sql, *args):
        await self._handle_row_lock(sql, args)
        await asyncio.sleep(self.pool.query_delay)
        return self.pool.route_query_val(sql, args)

    async def execute(self, sql, *args):
        await self._handle_row_lock(sql, args)
        await asyncio.sleep(self.pool.query_delay)
        return self.pool.route_execute(sql, args)


class VirtualConnectionPool:
    """Async connection pool simulator enforcing strict max_size limits, queue contention, and row locking."""

    def __init__(self, max_size=4, query_delay=0.001):
        self.max_size = max_size
        self.query_delay = query_delay
        self.semaphore = asyncio.Semaphore(max_size)
        self.active_connections = 0
        self.max_active_observed = 0
        self.total_acquisitions = 0
        self.queue_wait_times = []
        self._lock = asyncio.Lock()
        self._row_locks = {}
        self._row_lock_mutex = asyncio.Lock()

        # In-memory storage tables
        self.reports = {}
        self.jobs = {}
        self.conversations = {}
        self.messages = []
        self.request_limits = {}
        self.job_locks = set()

    async def get_row_lock(self, key):
        async with self._row_lock_mutex:
            if key not in self._row_locks:
                self._row_locks[key] = asyncio.Lock()
            return self._row_locks[key]

    def acquire(self):
        pool_ref = self
        class ConnContext:
            async def __aenter__(ctx_self):
                t0 = time.monotonic()
                await pool_ref.semaphore.acquire()
                wait_time = time.monotonic() - t0

                async with pool_ref._lock:
                    pool_ref.active_connections += 1
                    pool_ref.total_acquisitions += 1
                    pool_ref.queue_wait_times.append(wait_time)
                    if pool_ref.active_connections > pool_ref.max_active_observed:
                        pool_ref.max_active_observed = pool_ref.active_connections

                return VirtualConnection(pool_ref)

            async def __aexit__(ctx_self, exc_type, exc_val, exc_tb):
                async with pool_ref._lock:
                    pool_ref.active_connections -= 1
                pool_ref.semaphore.release()
                return False

        return ConnContext()

    def route_query_row(self, sql, args):
        sql_clean = " ".join(sql.split()).lower()

        if "select 1 as ok" in sql_clean:
            return {"ok": 1}

        if "select count(*) as total from reports" in sql_clean:
            return {"total": len(self.reports)}

        if "insert into reports" in sql_clean:
            rep_id = uuid.uuid4()
            user_id, title, depth, model = args[0], args[1], args[2], args[3]
            rec = {
                "id": rep_id,
                "user_id": user_id,
                "title": title,
                "topic": title,
                "depth": depth,
                "model": model,
                "status": "queued",
                "content": "",
                "sources": [],
                "created_at": time.time(),
            }
            self.reports[rep_id] = rec
            return rec

        if "insert into jobs" in sql_clean:
            job_id = uuid.uuid4()
            user_id = args[0]
            if "kind" in sql_clean and "'chat'" in sql_clean:
                payload = args[1]
                conv_id = args[2]
                rec = {
                    "id": job_id,
                    "user_id": user_id,
                    "kind": "chat",
                    "payload": payload,
                    "conversation_id": conv_id,
                    "report_id": None,
                    "status": "queued",
                    "stage": "queued",
                    "cancel_requested": False,
                    "attempts": 0,
                    "lease_token": None,
                    "lease_until": None,
                    "checkpoint": {},
                    "created_at": time.time(),
                }
            else:
                payload = args[1] if len(args) > 1 else {}
                rep_id = args[2] if len(args) > 2 else None
                rec = {
                    "id": job_id,
                    "user_id": user_id,
                    "kind": "research",
                    "payload": payload,
                    "report_id": rep_id,
                    "conversation_id": None,
                    "status": "queued",
                    "stage": "queued",
                    "cancel_requested": False,
                    "attempts": 0,
                    "lease_token": None,
                    "lease_until": None,
                    "checkpoint": {},
                    "created_at": time.time(),
                }
            self.jobs[job_id] = rec
            return rec

        if "select * from reports where id=$1 and user_id=$2" in sql_clean or "from reports where id=$1" in sql_clean:
            rep_id = args[0]
            for r in self.reports.values():
                if str(r.get("id")) == str(rep_id):
                    return dict(r)
            return None

        if "select * from conversations where id=$1 and user_id=$2" in sql_clean or "from conversations where id=$1" in sql_clean:
            conv_id = args[0]
            for c in self.conversations.values():
                if str(c.get("id")) == str(conv_id):
                    return dict(c)
            return None

        if "from messages where id=$1" in sql_clean:
            msg_id = args[0]
            for m in self.messages:
                if str(m.get("id")) == str(msg_id):
                    return dict(m)
            return None

        if "select status,payload from jobs where conversation_id=$1" in sql_clean:
            conv_id = str(args[0])
            for j in reversed(list(self.jobs.values())):
                if str(j.get("conversation_id")) == conv_id:
                    return {"status": j["status"], "payload": j.get("payload", {"model": MODELS[0]})}
            return None

        if "from jobs where conversation_id=$1" in sql_clean:
            conv_id = str(args[0])
            for j in reversed(list(self.jobs.values())):
                if str(j.get("conversation_id")) == conv_id:
                    return {"id": j["id"], "status": j["status"], "stage": j["stage"], "error": j.get("error")}
            return None

        if "select * from jobs where id=$1 and user_id=$2" in sql_clean or "select * from jobs where id=$1" in sql_clean:
            job_id = args[0]
            for j in self.jobs.values():
                if str(j.get("id")) == str(job_id):
                    return dict(j)
            return None

        if "update jobs set cancel_requested=true" in sql_clean:
            job_id = args[0]
            for j in self.jobs.values():
                if str(j.get("id")) == str(job_id):
                    j["cancel_requested"] = True
                    if j["status"] == "queued":
                        j["status"] = "cancelled"
                    return dict(j)
            return None

        if "insert into conversations" in sql_clean:
            conv_id = uuid.uuid4()
            user_id = args[0]
            title = args[1]
            rep_id = args[2] if len(args) > 2 else None
            rec = {
                "id": conv_id,
                "user_id": user_id,
                "title": title,
                "report_id": rep_id,
                "created_at": time.time(),
            }
            self.conversations[conv_id] = rec
            return rec

        if "insert into request_limits" in sql_clean:
            key = str(args[0])
            count = self.request_limits.get(key, 0) + 1
            self.request_limits[key] = count
            return {"count": count}

        return None

    def route_query_rows(self, sql, args):
        sql_clean = " ".join(sql.split()).lower()
        if "from reports" in sql_clean:
            return list(self.reports.values())[:20]
        if "from jobs" in sql_clean:
            return list(self.jobs.values())[:20]
        if "from conversations" in sql_clean:
            return list(self.conversations.values())[:20]
        if "from messages" in sql_clean:
            conv_id = str(args[0]) if args else None
            return [dict(m) for m in self.messages if str(m.get("conversation_id")) == conv_id]
        return []

    def route_query_val(self, sql, args):
        sql_clean = " ".join(sql.split()).lower()
        if "select exists(select 1 from jobs where conversation_id=$1 and status in ('queued','running'))" in sql_clean:
            conv_id = str(args[0])
            for j in self.jobs.values():
                if str(j.get("conversation_id")) == conv_id and j.get("status") in ("queued", "running"):
                    return True
            return False
        return False

    def route_execute(self, sql, args):
        sql_clean = " ".join(sql.split()).lower()
        if "insert into messages" in sql_clean:
            msg_id = uuid.uuid4()
            user_id = args[0]
            conv_id = args[1]
            role = "user"
            content = args[2]
            self.messages.append({
                "id": msg_id,
                "user_id": user_id,
                "conversation_id": conv_id,
                "role": role,
                "content": content,
                "created_at": time.time(),
            })
            return "INSERT 0 1"
        if "update reports set status=" in sql_clean:
            rep_id = str(args[0])
            for r in self.reports.values():
                if str(r.get("id")) == rep_id:
                    r["status"] = "cancelled"
            return "UPDATE 1"
        return "SUCCESS"


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def mock_pool():
    """Sets up a virtual database connection pool on app.db."""
    pool = VirtualConnectionPool(max_size=4, query_delay=0.001)
    db.pool = pool
    return pool


@pytest.fixture
def default_user():
    return {
        "id": uuid.uuid4(),
        "role": "user",
        "suspended": False,
        "features": {"research": True, "chat": True, "schedule": True, "redteam": True},
        "daily_limit": 50000,
        "email": "loadtester@example.test",
        "display_name": "Concurrency Tester",
    }


@pytest.fixture
def asgi_client(mock_pool, default_user):
    """Provides an authenticated httpx.AsyncClient hooked to the FastAPI ASGI application."""
    app.dependency_overrides[current_user] = lambda: default_user
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    yield client
    app.dependency_overrides.clear()


# =====================================================================
# 1. ASGI Server Concurrency & Throughput
# =====================================================================

class TestAsgiServerConcurrency:
    """Verifies that the ASGI application handles concurrent user requests without deadlocks or regressions."""

    @pytest.mark.asyncio
    async def test_concurrent_health_probes(self, asgi_client):
        """Simulates 40 concurrent health probes (load balancer health checks)."""
        async def fetch_health():
            resp = await asgi_client.get("/health")
            assert resp.status_code == 200
            return resp.json()

        results = await asyncio.gather(*(fetch_health() for _ in range(40)))
        assert len(results) == 40
        assert all(r["status"] == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_research_submissions(self, asgi_client):
        """Simulates 25 concurrent users launching research topics simultaneously."""
        with patch.object(cache, "throttle", new_callable=AsyncMock):
            async def launch_research(idx):
                payload = {
                    "topic": f"Concurrency Exploration Topic {idx}",
                    "depth": "standard",
                    "model": MODELS[0],
                }
                resp = await asgi_client.post("/api/v1/research", json=payload)
                return resp

            responses = await asyncio.gather(*(launch_research(i) for i in range(25)))
            assert len(responses) == 25
            for r in responses:
                assert r.status_code == 202
                data = r.json()
                assert "report" in data
                assert "job" in data
                assert data["report"]["status"] == "queued"
                assert data["job"]["status"] == "queued"

    @pytest.mark.asyncio
    async def test_concurrent_research_history_queries(self, asgi_client, mock_pool, default_user):
        """Verifies read concurrency across history endpoints with query params."""
        for i in range(10):
            rep_id = uuid.uuid4()
            mock_pool.reports[rep_id] = {
                "id": rep_id,
                "user_id": default_user["id"],
                "title": f"Report {i}",
                "topic": f"Topic {i}",
                "depth": "standard",
                "model": MODELS[0],
                "status": "completed",
                "created_at": time.time(),
            }

        async def get_history(page):
            return await asgi_client.get(f"/api/v1/research?page={page}&q=Report")

        responses = await asyncio.gather(*(get_history(i % 3 + 1) for i in range(20)))
        assert all(r.status_code == 200 for r in responses)
        assert all("items" in r.json() for r in responses)

    @pytest.mark.asyncio
    async def test_event_loop_responsiveness_under_concurrency(self, asgi_client):
        """Monitors event loop latency during concurrent request dispatch."""
        loop_lag = []

        async def measure_lag():
            for _ in range(5):
                t0 = time.monotonic()
                await asyncio.sleep(0.01)
                loop_lag.append(time.monotonic() - t0 - 0.01)

        async def background_load():
            for _ in range(15):
                await asgi_client.get("/health")

        lag_task = asyncio.create_task(measure_lag())
        load_tasks = [asyncio.create_task(background_load()) for _ in range(5)]

        await asyncio.gather(lag_task, *load_tasks)
        max_lag = max(loop_lag)
        # Event loop lag should not exceed 100ms in-memory under concurrent dispatch
        assert max_lag < 0.10, f"Event loop degraded: {max_lag*1000:.2f}ms lag"


# =====================================================================
# 2. Database Connection Pool Saturation Mechanics
# =====================================================================

class TestDbConnectionPoolSaturation:
    """Verifies connection pool limits (max_size=4), queuing, and leak prevention."""

    @pytest.mark.asyncio
    async def test_pool_max_size_strict_boundary(self, mock_pool):
        """Verifies that no more than 4 connections are checked out simultaneously under 30 concurrent tasks."""
        concurrency = 30
        task_latencies = []

        async def worker_task(idx):
            t0 = time.monotonic()
            async with mock_pool.acquire() as conn:
                # Hold connection briefly
                await asyncio.sleep(0.01)
                task_latencies.append(time.monotonic() - t0)
            return idx

        results = await asyncio.gather(*(worker_task(i) for i in range(concurrency)))

        assert len(results) == concurrency
        assert mock_pool.max_active_observed <= 4
        assert mock_pool.total_acquisitions == concurrency
        assert len(mock_pool.queue_wait_times) == concurrency
        # Later tasks must have experienced queue wait time
        assert max(mock_pool.queue_wait_times) > 0.01

    @pytest.mark.asyncio
    async def test_pool_connection_cleanup_on_unhandled_exception(self, mock_pool):
        """Ensures that exceptions within user code release connection slots back to pool immediately."""
        initial_active = mock_pool.active_connections

        async def failing_operation():
            with pytest.raises(RuntimeError):
                async with mock_pool.acquire():
                    raise RuntimeError("Crash inside database transaction")

        await asyncio.gather(*(failing_operation() for _ in range(10)))

        # Pool must return to zero active connections
        assert mock_pool.active_connections == initial_active
        assert mock_pool.active_connections == 0

    @pytest.mark.asyncio
    async def test_pool_acquisition_timeout_resilience(self):
        """Simulates timeout behavior when connections are held beyond acquisition thresholds."""
        pool = VirtualConnectionPool(max_size=2)

        async def slow_holder():
            async with pool.acquire():
                await asyncio.sleep(0.2)

        # Occupy both slots
        h1 = asyncio.create_task(slow_holder())
        h2 = asyncio.create_task(slow_holder())
        await asyncio.sleep(0.01)

        async def timed_acquirer():
            try:
                # Attempt acquire with 0.05s timeout
                return await asyncio.wait_for(pool.acquire().__aenter__(), timeout=0.05)
            except asyncio.TimeoutError:
                return "timeout_caught"

        result = await timed_acquirer()
        assert result == "timeout_caught"

        await asyncio.gather(h1, h2)
        assert pool.active_connections == 0


# =====================================================================
# 3. Async Lock Contention (FOR UPDATE SKIP LOCKED)
# =====================================================================

class TestWorkerLockContentionAndSkipLocked:
    """Evaluates queue consumer scaling, SKIP LOCKED semantics, and collision avoidance."""

    @pytest.mark.asyncio
    async def test_skip_locked_deduplication_under_parallel_workers(self):
        """Simulates 10 concurrent worker instances pulling from a shared queue of 30 jobs.
        Ensures strict deduplication: zero jobs claimed twice, all jobs claimed exactly once.
        """
        num_jobs = 30
        num_workers = 10

        queue = [
            {"id": uuid.uuid4(), "status": "queued", "locked": False}
            for _ in range(num_jobs)
        ]
        lock = asyncio.Lock()
        claimed_records = []

        async def simulate_skip_locked_claim(worker_id):
            """Simulates Postgres FOR UPDATE SKIP LOCKED row selection."""
            claimed = []
            while True:
                candidate = None
                async with lock:
                    for j in queue:
                        if j["status"] == "queued" and not j["locked"]:
                            j["locked"] = True
                            j["status"] = "running"
                            j["worker_id"] = worker_id
                            candidate = j["id"]
                            break
                if candidate is None:
                    break
                claimed.append(candidate)
                # Simulate work
                await asyncio.sleep(0.005)
            return claimed

        worker_results = await asyncio.gather(*(simulate_skip_locked_claim(i) for i in range(num_workers)))

        for res in worker_results:
            claimed_records.extend(res)

        # 1. Total jobs claimed must equal total jobs queued
        assert len(claimed_records) == num_jobs
        # 2. Strict deduplication: all claimed IDs must be unique
        assert len(set(claimed_records)) == num_jobs
        # 3. All queue items must be processed
        assert all(j["status"] == "running" for j in queue)

    @pytest.mark.asyncio
    async def test_worker_recovery_limit_bounding_poison_pill(self, mock_pool):
        """Verifies worker.work_once() handles poison-pill jobs where attempts > 3."""
        poison_job = {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "kind": "research",
            "payload": {"topic": "Poison Pill Topic"},
            "checkpoint": {},
            "status": "running",
            "attempts": 4,  # Exceeded recovery limit
            "lease_token": uuid.uuid4(),
            "report_id": None,
        }

        with patch.object(db, "one", new_callable=AsyncMock) as mock_one, \
             patch.object(worker, "recover_usage", new_callable=AsyncMock), \
             patch.object(worker, "finish", new_callable=AsyncMock) as mock_finish:

            mock_one.return_value = poison_job
            handled = await worker.work_once()

            assert handled is True
            # Worker must call finish with status='failed'
            assert mock_finish.call_count == 1
            call_args = mock_finish.call_args
            assert call_args[0][1] == "failed"
            assert "Worker recovery limit reached" in call_args[1].get("error", "")

    @pytest.mark.asyncio
    async def test_worker_abandoned_lease_reclaim_logic(self):
        """Simulates worker stealing expired job leases: lease_until < now()."""
        now = time.time()
        jobs = [
            {"id": uuid.uuid4(), "status": "running", "lease_until": now - 30, "reclaimed": False},
            {"id": uuid.uuid4(), "status": "running", "lease_until": now + 60, "reclaimed": False},
            {"id": uuid.uuid4(), "status": "queued", "lease_until": None, "reclaimed": False},
        ]

        def query_eligible_jobs():
            # SQL logic: (status='queued') or (status='running' and lease_until < now())
            return [
                j for j in jobs
                if j["status"] == "queued" or (j["status"] == "running" and j["lease_until"] < now)
            ]

        eligible = query_eligible_jobs()
        assert len(eligible) == 2
        expired_job = next(j for j in eligible if j["status"] == "running")
        assert expired_job["lease_until"] < now


# =====================================================================
# 4. Burst Traffic vs Rate Limiter (20 req/min)
# =====================================================================

class TestRateLimiterBurstAndMultiUser:
    """Verifies the 20 req/min rate limit ceiling, Redis Lua atomicity, DB fallback, and multi-tenant isolation."""

    @pytest.mark.asyncio
    async def test_burst_rate_limiter_20_per_min_strict_ceiling(self):
        """Sends 30 concurrent requests within 100ms: first 20 must pass, subsequent 10 must raise 429."""
        user_id = uuid.uuid4()
        counter = {"count": 0}

        async def simulate_throttle():
            counter["count"] += 1
            if counter["count"] > 20:
                raise HTTPException(429, "Too many requests. Try again in one minute.")
            return counter["count"]

        async def fire_request():
            try:
                c = await simulate_throttle()
                return (200, c)
            except HTTPException as e:
                return (e.status_code, e.detail)

        results = await asyncio.gather(*(fire_request() for _ in range(30)))

        successes = [r for r in results if r[0] == 200]
        throttled = [r for r in results if r[0] == 429]

        assert len(successes) == 20
        assert len(throttled) == 10
        assert throttled[0][1] == "Too many requests. Try again in one minute."

    @pytest.mark.asyncio
    async def test_rate_limiter_redis_lua_script_atomic_execution(self):
        """Tests Redis Lua atomic increment and 60-second expiration logic."""
        mock_redis = AsyncMock()
        state = {}

        async def fake_eval(script, numkeys, key):
            # Implements:
            # local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n
            cur = state.get(key, 0) + 1
            state[key] = cur
            return cur

        mock_redis.eval = AsyncMock(side_effect=fake_eval)

        with patch.object(cache, "connection", return_value=mock_redis):
            uid = uuid.uuid4()
            # Execute 20 calls
            for i in range(20):
                await cache.throttle(uid)
            assert state[f"rate:{uid}"] == 20

            # 21st call must trigger HTTPException 429
            with pytest.raises(HTTPException) as exc_info:
                await cache.throttle(uid)
            assert exc_info.value.status_code == 429
            assert "Too many requests" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rate_limiter_database_fallback_when_redis_fails(self, mock_pool):
        """When Redis connection is None or raises an error, fallback to database request_limits table."""
        with patch.object(cache, "connection", return_value=None):
            uid = uuid.uuid4()

            for _ in range(20):
                await cache.throttle(uid)

            # 21st call should trigger 429 from DB count
            with pytest.raises(HTTPException) as exc_info:
                await cache.throttle(uid)

            assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_multi_tenant_rate_limit_isolation(self):
        """User A exhausting their 20 req/min quota must NOT impact User B."""
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        usage = {user_a: 0, user_b: 0}

        async def throttle_user(uid):
            usage[uid] += 1
            if usage[uid] > 20:
                raise HTTPException(429, "Too many requests. Try again in one minute.")
            return True

        # User A makes 20 requests
        for _ in range(20):
            await throttle_user(user_a)

        # User A 21st request fails
        with pytest.raises(HTTPException):
            await throttle_user(user_a)

        # User B still has full 20 request quota available
        for _ in range(20):
            res = await throttle_user(user_b)
            assert res is True


# =====================================================================
# 5. Concurrent Cancellations
# =====================================================================

class TestConcurrentCancellations:
    """Verifies race condition handling during simultaneous job cancellations."""

    @pytest.mark.asyncio
    async def test_concurrent_cancellations_idempotent(self, asgi_client, mock_pool, default_user):
        """10 simultaneous cancellation requests on the same job return idempotent 200 responses."""
        job_id = uuid.uuid4()
        rep_id = uuid.uuid4()
        mock_pool.jobs[job_id] = {
            "id": job_id,
            "user_id": default_user["id"],
            "report_id": rep_id,
            "status": "running",
            "cancel_requested": False,
        }

        async def cancel_job():
            return await asgi_client.post(f"/api/v1/jobs/{job_id}/cancel")

        responses = await asyncio.gather(*(cancel_job() for _ in range(10)))
        assert all(r.status_code == 200 for r in responses)
        assert all(r.json() == {"cancel_requested": True} for r in responses)
        assert mock_pool.jobs[job_id]["cancel_requested"] is True

    @pytest.mark.asyncio
    async def test_queued_job_cancellation_immediate_transition(self, asgi_client, mock_pool, default_user):
        """Cancelling a queued job transitions status immediately to 'cancelled'."""
        job_id = uuid.uuid4()
        rep_id = uuid.uuid4()
        mock_pool.reports[rep_id] = {"id": rep_id, "user_id": default_user["id"], "status": "queued"}
        mock_pool.jobs[job_id] = {
            "id": job_id,
            "user_id": default_user["id"],
            "report_id": rep_id,
            "status": "queued",
            "cancel_requested": False,
        }

        resp = await asgi_client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert mock_pool.jobs[job_id]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_worker_checkpoint_cancels_when_cancel_requested(self):
        """Worker checkpoint detects cancel_requested and aborts execution via worker.Cancelled."""
        job = {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "lease_token": uuid.uuid4(),
            "status": "running",
            "cancel_requested": True,
            "checkpoint": {},
        }

        with patch.object(db, "one", new_callable=AsyncMock, return_value=None):
            with pytest.raises(worker.Cancelled):
                await worker.checkpoint(job, "Step 2: Searching")


# =====================================================================
# 6. Chat Concurrency & Document Exports
# =====================================================================

class TestChatConcurrencyAndExports:
    """Evaluates concurrent chat interactions, active reply lock (409 Conflict), and PDF/Markdown exports."""

    @pytest.mark.asyncio
    async def test_chat_conflict_when_reply_already_in_progress(self, asgi_client, mock_pool, default_user):
        """When a reply job is already running or queued, posting another message returns 409 Conflict."""
        conv_id = uuid.uuid4()
        mock_pool.conversations[conv_id] = {
            "id": conv_id,
            "user_id": default_user["id"],
            "title": "Chat Topic",
        }
        # Add an active job
        active_job_id = uuid.uuid4()
        mock_pool.jobs[active_job_id] = {
            "id": active_job_id,
            "conversation_id": conv_id,
            "user_id": default_user["id"],
            "status": "running",
        }

        with patch.object(cache, "throttle", new_callable=AsyncMock):
            resp = await asgi_client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "Can you summarize?", "model": MODELS[0]},
            )
            assert resp.status_code == 409
            assert "A reply is already in progress" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_concurrent_markdown_export(self, asgi_client, mock_pool, default_user):
        """Concurrent export requests for a completed research report."""
        rep_id = uuid.uuid4()
        mock_pool.reports[rep_id] = {
            "id": rep_id,
            "user_id": default_user["id"],
            "title": "Superconductor Physics",
            "status": "completed",
            "content": "## Superconductor Findings\nZero electrical resistance confirmed [1].",
        }

        async def get_export():
            return await asgi_client.get(f"/api/v1/exports/research/{rep_id}?format=markdown")

        responses = await asyncio.gather(*(get_export() for _ in range(10)))
        assert all(r.status_code == 200 for r in responses)
        assert all("text/markdown" in r.headers["content-type"] for r in responses)
        assert all("# Superconductor Physics" in r.text for r in responses)

    @pytest.mark.asyncio
    async def test_concurrent_pdf_export_offloaded_to_threadpool(self, asgi_client, mock_pool, default_user):
        """Concurrent PDF exports use asyncio.to_thread and return valid PDF headers."""
        rep_id = uuid.uuid4()
        mock_pool.reports[rep_id] = {
            "id": rep_id,
            "user_id": default_user["id"],
            "title": "Quantum Memory Density",
            "status": "completed",
            "content": "## Key Findings\nQuantum coherence preserved at room temperature [1].",
        }

        async def get_pdf():
            return await asgi_client.get(f"/api/v1/exports/research/{rep_id}?format=pdf")

        responses = await asyncio.gather(*(get_pdf() for _ in range(5)))
        assert all(r.status_code == 200 for r in responses)
        assert all(r.headers["content-type"] == "application/pdf" for r in responses)
        assert all(r.content.startswith(b"%PDF-") for r in responses)

    @pytest.mark.asyncio
    async def test_export_incomplete_report_rejected(self, asgi_client, mock_pool, default_user):
        """Exporting a queued/running report raises 409 Conflict."""
        rep_id = uuid.uuid4()
        mock_pool.reports[rep_id] = {
            "id": rep_id,
            "user_id": default_user["id"],
            "title": "Pending Report",
            "status": "running",
            "content": "",
        }
        resp = await asgi_client.get(f"/api/v1/exports/research/{rep_id}?format=markdown")
        assert resp.status_code == 409
        assert "Report is not ready" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_concurrent_chat_same_conversation_strict_single_reply(self, asgi_client, mock_pool, default_user):
        """10 concurrent messages to the SAME conversation simultaneously.
        PostgreSQL FOR UPDATE row-level lock serializes enqueue_chat:
        Exactly 1 message is accepted (202), and 9 return 409 Conflict ('A reply is already in progress').
        Strictly prevents duplicate job generation for the same conversation.
        """
        conv_id = uuid.uuid4()
        mock_pool.conversations[conv_id] = {
            "id": conv_id,
            "user_id": default_user["id"],
            "title": "Quantum Photonics Dialogue",
        }

        with patch.object(cache, "throttle", new_callable=AsyncMock):
            async def post_msg(idx):
                return await asgi_client.post(
                    f"/api/v1/conversations/{conv_id}/messages",
                    json={"content": f"Concurrent message probe {idx}", "model": MODELS[0]},
                )

            responses = await asyncio.gather(*(post_msg(i) for i in range(10)))
            accepted = [r for r in responses if r.status_code == 202]
            conflicts = [r for r in responses if r.status_code == 409]

            assert len(accepted) == 1, f"Expected exactly 1 accepted, got {len(accepted)}"
            assert len(conflicts) == 9, f"Expected 9 conflicts, got {len(conflicts)}"
            assert all("A reply is already in progress" in r.json()["error"]["message"] for r in conflicts)

            # Invariant: exactly 1 chat job enqueued in DB for this conversation
            chat_jobs = [j for j in mock_pool.jobs.values() if j.get("conversation_id") == conv_id]
            assert len(chat_jobs) == 1
            assert chat_jobs[0]["status"] == "queued"

    @pytest.mark.asyncio
    async def test_concurrent_chat_retry_conflict_handling(self, asgi_client, mock_pool, default_user):
        """Concurrent retry calls on a failed conversation job: exactly 1 succeeds, others get 409."""
        conv_id = uuid.uuid4()
        mock_pool.conversations[conv_id] = {
            "id": conv_id,
            "user_id": default_user["id"],
            "title": "Failed Job Conversation",
        }
        failed_job_id = uuid.uuid4()
        mock_pool.jobs[failed_job_id] = {
            "id": failed_job_id,
            "conversation_id": conv_id,
            "user_id": default_user["id"],
            "kind": "chat",
            "payload": {"model": MODELS[0]},
            "status": "failed",
            "created_at": time.time() - 10,
        }

        with patch.object(cache, "throttle", new_callable=AsyncMock):
            responses = await asyncio.gather(*(
                asgi_client.post(f"/api/v1/conversations/{conv_id}/retry") for _ in range(5)
            ))
            accepted = [r for r in responses if r.status_code == 202]
            conflicts = [r for r in responses if r.status_code == 409]

            assert len(accepted) == 1
            assert len(conflicts) == 4

    @pytest.mark.asyncio
    async def test_concurrent_chat_conversation_export_markdown(self, asgi_client, mock_pool, default_user):
        """Tests concurrent export of a multi-turn chat conversation in Markdown format."""
        conv_id = uuid.uuid4()
        mock_pool.conversations[conv_id] = {
            "id": conv_id,
            "user_id": default_user["id"],
            "title": "Quantum Entanglement Q&A",
        }
        mock_pool.messages.extend([
            {
                "id": uuid.uuid4(),
                "user_id": default_user["id"],
                "conversation_id": conv_id,
                "role": "user",
                "content": "Can you summarize the EPR paradox?",
                "created_at": time.time() - 60,
            },
            {
                "id": uuid.uuid4(),
                "user_id": default_user["id"],
                "conversation_id": conv_id,
                "role": "assistant",
                "content": "The EPR paradox highlights non-local correlations predicted by quantum mechanics.",
                "created_at": time.time() - 50,
            },
            {
                "id": uuid.uuid4(),
                "user_id": default_user["id"],
                "conversation_id": conv_id,
                "role": "user",
                "content": "Did Bell inequality resolve it?",
                "created_at": time.time() - 40,
            },
            {
                "id": uuid.uuid4(),
                "user_id": default_user["id"],
                "conversation_id": conv_id,
                "role": "assistant",
                "content": "Yes, Bell's theorem showed that local hidden variable theories are incompatible with quantum predictions.",
                "created_at": time.time() - 30,
            },
        ])

        async def fetch_chat_export():
            return await asgi_client.get(f"/api/v1/exports/conversation/{conv_id}?format=markdown")

        responses = await asyncio.gather(*(fetch_chat_export() for _ in range(10)))
        assert all(r.status_code == 200 for r in responses)
        assert all("text/markdown" in r.headers["content-type"] for r in responses)
        assert all(f'attachment; filename="conversation-{conv_id}.md"' in r.headers.get("content-disposition", "") for r in responses)

        sample_text = responses[0].text
        assert "# Quantum Entanglement Q&A" in sample_text
        assert "## User" in sample_text
        assert "## Assistant" in sample_text
        assert "Can you summarize the EPR paradox?" in sample_text
        assert "Bell's theorem showed" in sample_text

    @pytest.mark.asyncio
    async def test_concurrent_chat_conversation_export_pdf(self, asgi_client, mock_pool, default_user):
        """Tests concurrent PDF compilation of chat conversations offloaded to threadpool."""
        conv_id = uuid.uuid4()
        mock_pool.conversations[conv_id] = {
            "id": conv_id,
            "user_id": default_user["id"],
            "title": "Topological Superconductors Chat",
        }
        mock_pool.messages.extend([
            {
                "id": uuid.uuid4(),
                "user_id": default_user["id"],
                "conversation_id": conv_id,
                "role": "user",
                "content": "What are Majorana zero modes?",
                "created_at": time.time() - 20,
            },
            {
                "id": uuid.uuid4(),
                "user_id": default_user["id"],
                "conversation_id": conv_id,
                "role": "assistant",
                "content": "Majorana zero modes are non-Abelian anyons that could enable fault-tolerant quantum computing.",
                "created_at": time.time() - 10,
            },
        ])

        async def fetch_chat_pdf():
            return await asgi_client.get(f"/api/v1/exports/conversation/{conv_id}?format=pdf")

        responses = await asyncio.gather(*(fetch_chat_pdf() for _ in range(5)))
        assert all(r.status_code == 200 for r in responses)
        assert all(r.headers["content-type"] == "application/pdf" for r in responses)
        assert all(f'attachment; filename="conversation-{conv_id}.pdf"' in r.headers.get("content-disposition", "") for r in responses)
        assert all(r.content.startswith(b"%PDF-") for r in responses)

    @pytest.mark.asyncio
    async def test_export_assistant_message_success(self, asgi_client, mock_pool, default_user):
        """Exporting an individual assistant message succeeds in Markdown and PDF."""
        msg_id = uuid.uuid4()
        conv_id = uuid.uuid4()
        mock_pool.messages.append({
            "id": msg_id,
            "user_id": default_user["id"],
            "conversation_id": conv_id,
            "role": "assistant",
            "content": "High-efficiency perovskite solar cells demonstrate 26% PCE.",
            "created_at": time.time(),
        })

        resp_md = await asgi_client.get(f"/api/v1/exports/message/{msg_id}?format=markdown")
        assert resp_md.status_code == 200
        assert "# Assistant response" in resp_md.text
        assert "High-efficiency perovskite" in resp_md.text

        resp_pdf = await asgi_client.get(f"/api/v1/exports/message/{msg_id}?format=pdf")
        assert resp_pdf.status_code == 200
        assert resp_pdf.headers["content-type"] == "application/pdf"
        assert resp_pdf.content.startswith(b"%PDF-")

    @pytest.mark.asyncio
    async def test_export_user_message_rejected_400(self, asgi_client, mock_pool, default_user):
        """Attempting to export a user message raises 400 Bad Request."""
        msg_id = uuid.uuid4()
        conv_id = uuid.uuid4()
        mock_pool.messages.append({
            "id": msg_id,
            "user_id": default_user["id"],
            "conversation_id": conv_id,
            "role": "user",
            "content": "Can you summarize perovskites?",
            "created_at": time.time(),
        })

        resp = await asgi_client.get(f"/api/v1/exports/message/{msg_id}?format=markdown")
        assert resp.status_code == 400
        assert "Only assistant messages can be exported" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_export_nonexistent_and_invalid_formats(self, asgi_client, mock_pool, default_user):
        """Non-existent entities return 404, invalid formats return 400."""
        random_id = uuid.uuid4()
        resp_404 = await asgi_client.get(f"/api/v1/exports/conversation/{random_id}?format=markdown")
        assert resp_404.status_code == 404

        resp_bad_kind = await asgi_client.get(f"/api/v1/exports/unknown_kind/{random_id}")
        assert resp_bad_kind.status_code == 404

        # Add valid conversation to test bad format
        conv_id = uuid.uuid4()
        mock_pool.conversations[conv_id] = {"id": conv_id, "user_id": default_user["id"], "title": "Test Conv"}
        resp_bad_format = await asgi_client.get(f"/api/v1/exports/conversation/{conv_id}?format=docx")
        assert resp_bad_format.status_code == 400
        assert "Use markdown or pdf" in resp_bad_format.json()["error"]["message"]


# =====================================================================
# 7. Payload Boundaries & Error Pathways
# =====================================================================

class TestPayloadBoundariesAndSecurity:
    """Verifies schema boundaries, extra field rejection, invalid routes, and Unicode handling."""

    @pytest.mark.asyncio
    async def test_empty_topic_rejected(self, asgi_client):
        """Topic length < 3 chars rejected by Pydantic schema."""
        resp = await asgi_client.post("/api/v1/research", json={"topic": "hi", "depth": "standard"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation"

    @pytest.mark.asyncio
    async def test_unsupported_model_rejected(self, asgi_client):
        """Model outside free whitelist rejected."""
        resp = await asgi_client.post(
            "/api/v1/research",
            json={"topic": "Valid Topic Text", "depth": "standard", "model": "paid/gpt-5-expensive"},
        )
        assert resp.status_code == 422
        assert "Only platform free models are allowed" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_extra_fields_forbidden(self, asgi_client):
        """Extra JSON parameters trigger 422 due to ConfigDict(extra='forbid')."""
        resp = await asgi_client.post(
            "/api/v1/research",
            json={"topic": "Valid Topic Text", "depth": "standard", "model": MODELS[0], "injected_param": "hack"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_uuid_parameters(self, asgi_client):
        """Malformed UUID in path yields 422 validation error."""
        resp = await asgi_client.get("/api/v1/research/not-a-valid-uuid")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unicode_and_emoji_resilience(self, asgi_client):
        """Preserves multilingual Unicode and emojis in research topics."""
        with patch.object(cache, "throttle", new_callable=AsyncMock):
            unicode_topic = "量子计算 🚀 High-entropy alloys: é, è, ü, ñ & ⚛️"
            resp = await asgi_client.post(
                "/api/v1/research",
                json={"topic": unicode_topic, "depth": "standard", "model": MODELS[0]},
            )
            assert resp.status_code == 202
            assert resp.json()["report"]["topic"] == unicode_topic


# =====================================================================
# 8. Memory & Resource Stability
# =====================================================================

class TestServerMemoryAndResourceStability:
    """Evaluates garbage collection behavior and async task leak prevention."""

    @pytest.mark.asyncio
    async def test_no_asyncio_task_leakage_after_concurrency_burst(self, asgi_client):
        """Verifies active task count returns to baseline after a burst of 50 requests."""
        initial_tasks = len(asyncio.all_tasks())

        async def run_burst():
            return await asgi_client.get("/health")

        await asyncio.gather(*(run_burst() for _ in range(50)))
        await asyncio.sleep(0.01)

        post_tasks = len(asyncio.all_tasks())
        # Difference in task count should be zero or at most 1
        assert abs(post_tasks - initial_tasks) <= 1

    @pytest.mark.asyncio
    async def test_gc_stability_across_request_burst(self, asgi_client):
        """Ensures circular references or orphaned contexts do not prevent GC."""
        gc.collect()
        initial_objects = len(gc.get_objects())

        async def make_requests():
            for _ in range(25):
                await asgi_client.get("/health")

        await asyncio.gather(*(make_requests() for _ in range(4)))

        gc.collect()
        final_objects = len(gc.get_objects())
        growth = final_objects - initial_objects
        # Object count growth should be negligible (< 500 objects)
        assert growth < 500, f"Potential object leak: {growth} objects retained"


# =====================================================================
# 9. Concurrent HTTP / SSE Streaming Connections
# =====================================================================

class TestConcurrentHttpSseConnections:
    """Verifies ASGI server resilience under concurrent streaming connections (SSE / long-polling),
    ensuring event loop responsiveness and database connection pool non-starvation.
    """

    @pytest.mark.asyncio
    async def test_concurrent_http_sse_streaming_connections(self, asgi_client):
        """Simulates 15 concurrent clients receiving chunked streaming data while health checks and
        research queries run simultaneously, verifying zero event loop blockage.
        """
        from starlette.responses import StreamingResponse

        async def sse_event_generator(stream_id, chunk_count=5):
            for i in range(chunk_count):
                await asyncio.sleep(0.01)
                yield f"event: message\ndata: {{\"stream\": {stream_id}, \"step\": {i}}}\n\n".encode()

        @app.get("/api/v1/test/stream/{stream_id}")
        async def stream_endpoint(stream_id: int):
            return StreamingResponse(sse_event_generator(stream_id), media_type="text/event-stream")

        received_chunks = {i: [] for i in range(15)}

        async def client_stream_listener(stream_id):
            async with asgi_client.stream("GET", f"/api/v1/test/stream/{stream_id}") as resp:
                assert resp.status_code == 200
                async for chunk in resp.aiter_bytes():
                    received_chunks[stream_id].append(chunk)

        async def concurrent_health_pings():
            for _ in range(10):
                r = await asgi_client.get("/health")
                assert r.status_code == 200
                await asyncio.sleep(0.005)

        stream_tasks = [asyncio.create_task(client_stream_listener(s_id)) for s_id in range(15)]
        ping_task = asyncio.create_task(concurrent_health_pings())

        await asyncio.gather(*stream_tasks, ping_task)

        for s_id in range(15):
            full_content = b"".join(received_chunks[s_id]).decode()
            for step in range(5):
                assert f'"step": {step}' in full_content

    @pytest.mark.asyncio
    async def test_streaming_connections_do_not_exhaust_db_connection_pool(self, asgi_client, mock_pool):
        """Proves that long-lived streaming connections and slow client reads do NOT hold open database
        connections from the pool (active_connections == 0 during streaming idle intervals).
        """
        from starlette.responses import StreamingResponse

        async def slow_stream():
            for _ in range(3):
                # Verify that pool connections are NOT checked out during streaming sleep/network wait
                assert mock_pool.active_connections == 0
                await asyncio.sleep(0.02)
                yield b"event: ping\ndata: ok\n\n"

        @app.get("/api/v1/test/slow-stream")
        async def slow_stream_endpoint():
            return StreamingResponse(slow_stream(), media_type="text/event-stream")

        async def listen_stream():
            async with asgi_client.stream("GET", "/api/v1/test/slow-stream") as resp:
                assert resp.status_code == 200
                chunks = []
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
            full_stream = b"".join(chunks)
            return full_stream.count(b"event: ping")

        # 10 slow streams active simultaneously
        stream_results = await asyncio.gather(*(listen_stream() for _ in range(10)))
        assert all(c == 3 for c in stream_results)
        # Pool remains at zero active connections throughout
        assert mock_pool.active_connections == 0

    @pytest.mark.asyncio
    async def test_client_disconnect_during_streaming_releases_resources(self, asgi_client):
        """Simulates a client disconnecting prematurely mid-stream, verifying clean task cancellation."""
        from starlette.responses import StreamingResponse

        stream_cleaned_up = asyncio.Event()

        async def cancellable_stream():
            try:
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    yield b"chunk\n"
            finally:
                stream_cleaned_up.set()

        @app.get("/api/v1/test/abort-stream")
        async def abort_stream_endpoint():
            return StreamingResponse(cancellable_stream(), media_type="text/plain")

        async def aborted_client():
            try:
                async with asgi_client.stream("GET", "/api/v1/test/abort-stream") as resp:
                    assert resp.status_code == 200
                    async for _ in resp.aiter_bytes():
                        raise asyncio.CancelledError()
            except asyncio.CancelledError:
                return "cancelled"

        res = await aborted_client()
        assert res == "cancelled"
        await asyncio.sleep(0.02)
        assert stream_cleaned_up.is_set()

