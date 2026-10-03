#!/usr/bin/env bash
# Creates and removes its own isolated PostgreSQL cluster. Never uses cloud data.
set -euo pipefail
V2_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PG_BIN="${PG_BIN:-$(pg_config --bindir)}"
TEST_PORT="${TEST_PORT:-55441}"
TEST_CLUSTER="$(mktemp -d /tmp/research-v2-tests.XXXXXX)"
TEST_BACKEND_PID=''
cleanup() {
  if [ -n "$TEST_BACKEND_PID" ]; then kill "$TEST_BACKEND_PID" 2>/dev/null || true; wait "$TEST_BACKEND_PID" 2>/dev/null || true; fi
  "$PG_BIN/pg_ctl" -D "$TEST_CLUSTER/data" stop -m fast >/dev/null 2>&1 || true
  rm -rf -- "$TEST_CLUSTER"
}
trap cleanup EXIT
"$PG_BIN/initdb" -D "$TEST_CLUSTER/data" -A trust -U research_test >"$TEST_CLUSTER/init.log"
"$PG_BIN/pg_ctl" -D "$TEST_CLUSTER/data" -l "$TEST_CLUSTER/postgres.log" -o "-p $TEST_PORT -h 127.0.0.1 -k $TEST_CLUSTER" start >/dev/null
export TEST_DATABASE_URL="postgresql://research_test@127.0.0.1:$TEST_PORT/postgres"
export PYTHONPATH="$V2_ROOT/backend"
"$V2_ROOT/backend/.venv/bin/pytest" "$V2_ROOT/backend/tests" -q
if [ "${E2E:-0}" = '1' ]; then
  "$PG_BIN/psql" "$TEST_DATABASE_URL" -c 'create database research_browser' >/dev/null
  export TEST_DATABASE_URL="postgresql://research_test@127.0.0.1:$TEST_PORT/research_browser"
  "$V2_ROOT/backend/.venv/bin/python" "$V2_ROOT/backend/tests/browser_server.py"
  "$V2_ROOT/backend/.venv/bin/uvicorn" tests.browser_server:app --host 127.0.0.1 --port 8001 >"$TEST_CLUSTER/browser.log" 2>&1 &
  TEST_BACKEND_PID=$!
  for attempt in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1; then break; fi
    sleep 1
  done
  cd "$V2_ROOT/frontend"
  npm test
fi
