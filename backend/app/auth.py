import uuid
import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from . import db
from .config import settings

bearer = HTTPBearer(auto_error=False)


async def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, "Sign in required")
    s = settings()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                s.supabase_url.rstrip("/") + "/auth/v1/user",
                headers={"apikey": s.supabase_publishable_key, "Authorization": "Bearer " + credentials.credentials},
            )
        if response.status_code >= 500:
            raise HTTPException(503, "Authentication service unavailable")
        if response.status_code != 200:
            raise HTTPException(401, "Session expired; sign in again")
        uid = uuid.UUID(response.json()["id"])
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, "Unable to verify session")
    user = await db.one("select * from profiles where id=$1", uid)
    if not user:
        raise HTTPException(403, "Profile missing; apply database migrations")
    if user["suspended"]:
        raise HTTPException(403, "Account suspended")
    return user


async def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator access required")
    return user


def feature(user, name):
    if not user["features"].get(name, False):
        raise HTTPException(403, f"{name} is disabled for this account")
