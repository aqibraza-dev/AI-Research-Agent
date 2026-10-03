"""Read-only hosted smoke checks. TOKEN is a Supabase user access token, not an API key."""

import os
import sys

import httpx

base = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
token = os.environ.get("TOKEN")
if not token:
    sys.exit("Set TOKEN to a signed-in user access token")
with httpx.Client(timeout=90, headers={"Authorization": "Bearer " + token}) as c:
    for path in [
        "/health",
        "/api/v1/profile",
        "/api/v1/models",
        "/api/v1/research",
        "/api/v1/conversations",
        "/api/v1/schedules",
        "/api/v1/analytics",
        "/api/v1/notifications",
    ]:
        r = c.get(base + path)
        if r.status_code != 200:
            sys.exit(f"FAIL {path}: HTTP {r.status_code}")
        print("PASS", path)
    r = c.get(base + "/api/v1/profile", headers={"Authorization": ""})
    assert r.status_code == 401, "Unauthenticated access was not rejected"
    print("PASS unauthenticated request rejected")
