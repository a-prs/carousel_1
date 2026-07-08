"""Phase 2 — auth: HMAC multitenant + bcrypt + token_version + TTL."""
import time
import pytest
import auth
import db


async def test_hash_and_verify_password():
    h = auth.hash_password("hunter2")
    assert h != "hunter2"
    assert auth.verify_password("hunter2", h)
    assert not auth.verify_password("wrong", h)


async def test_valid_token_roundtrip(seeded_client):
    tok = auth.generate_token("vauforma", token_version=1)
    got = await auth.verify_token(tok)
    assert got is not None
    assert got["slug"] == "vauforma"
    assert got["id"] == seeded_client["id"]


async def test_invalid_signature_rejected(seeded_client):
    tok = auth.generate_token("vauforma", token_version=1)
    tampered = tok[:-4] + "0000"
    assert await auth.verify_token(tampered) is None


async def test_malformed_token_rejected(seeded_client):
    assert await auth.verify_token("garbage") is None
    assert await auth.verify_token("a:b") is None
    assert await auth.verify_token("") is None


async def test_expired_token_rejected(seeded_client, monkeypatch):
    old_ts = int(time.time()) - (auth.TOKEN_TTL + 100)
    tok = auth.generate_token("vauforma", token_version=1, ts=old_ts)
    assert await auth.verify_token(tok) is None


async def test_unknown_tenant_rejected(fresh_db):
    tok = auth.generate_token("ghost", token_version=1)
    assert await auth.verify_token(tok) is None


async def test_token_version_bump_invalidates_old_token(seeded_client):
    tok = auth.generate_token("vauforma", token_version=1)
    assert await auth.verify_token(tok) is not None
    # simulate reset-password bumping token_version
    await db.set_password("vauforma", auth.hash_password("new-pw"))
    assert await auth.verify_token(tok) is None  # old token now dead
    # a freshly minted token (v2) works
    c = await db.get_client_by_slug("vauforma")
    tok2 = auth.generate_token("vauforma", token_version=c["token_version"])
    assert await auth.verify_token(tok2) is not None


async def test_inactive_client_rejected(seeded_client):
    tok = auth.generate_token("vauforma", token_version=1)
    async with db._conn() as c:
        await c.execute("UPDATE clients SET active=0 WHERE slug='vauforma'")
        await c.commit()
    assert await auth.verify_token(tok) is None
