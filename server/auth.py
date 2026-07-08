"""auth.py — multitenant HMAC token + bcrypt + token_version + cookie.

Adapted from dashboard/server/app.py HMAC pattern, generalized to per-tenant
credentials in the `clients` table. HTTP Basic Auth is DELIBERATELY EXCLUDED
(plan §6 / §11.5) — this service must never offer Basic Auth.

Token = f"{slug}:{ts}:{sig}", sig = HMAC_SHA256(SECRET, f"{slug}:{token_version}:{ts}")[:32].
Including token_version means reset-password (which bumps it) invalidates every
already-issued token for that tenant (MAJOR M7). TTL 7 days (MAJOR M4).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request
from passlib.context import CryptContext

import db

TOKEN_SECRET = os.environ.get("CAROUSEL_TOKEN_SECRET", "")
TOKEN_TTL = 7 * 86400  # 7 days
COOKIE_NAME = "carousel_token"
COOKIE_PATH = "/carousel"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(password, hashed)
    except Exception:
        return False


def _sign(slug: str, token_version: int, ts: int) -> str:
    payload = f"{slug}:{token_version}:{ts}"
    return hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def generate_token(slug: str, token_version: int, ts: Optional[int] = None) -> str:
    if ts is None:
        ts = int(time.time())
    sig = _sign(slug, token_version, ts)
    return f"{slug}:{ts}:{sig}"


async def verify_token(token: str) -> Optional[dict]:
    """Return the client dict if the token is valid, else None."""
    if not token or not TOKEN_SECRET:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    slug, ts_s, sig = parts
    try:
        ts = int(ts_s)
    except ValueError:
        return None
    if time.time() - ts > TOKEN_TTL:
        return None
    client = await db.get_client_by_slug(slug)
    if not client or not client.get("active", 1):
        return None
    expected = _sign(slug, client["token_version"], ts)
    if not hmac.compare_digest(sig, expected):
        return None
    return client


class AuthedClient:
    """Resolved identity attached to a request (never trusts request body)."""
    def __init__(self, client: dict):
        self.id: int = client["id"]
        self.slug: str = client["slug"]
        self.display_name = client.get("display_name")
        self.ig_handle = client.get("ig_handle")
        self.raw = client


async def verify_auth(
    request: Request,
    authorization: str = Header(default=None),
    carousel_token: str = Cookie(default=None),
) -> AuthedClient:
    """FastAPI dependency: Bearer header first, cookie fallback (for <a download>)."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif carousel_token:
        token = carousel_token
    if token:
        client = await verify_token(token)
        if client:
            return AuthedClient(client)
    raise HTTPException(status_code=401, detail="Not authenticated")
