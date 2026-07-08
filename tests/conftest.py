"""Shared fixtures. Set env BEFORE importing app modules so paths/secrets are stable."""
import os
os.environ.setdefault("CAROUSEL_TOKEN_SECRET", "test-secret-fixed-abc123")
os.environ.setdefault("MODEL", "claude-sonnet-5")

import pytest
import db


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CAROUSEL_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "carousel.db")
    await db.apply_schema()
    return db


@pytest.fixture
async def seeded_client(fresh_db):
    import auth
    cid = await db.create_client(
        slug="vauforma",
        password_hash=auth.hash_password("s3cret-pw"),
        design_tokens={"bg": "#0b1220", "accent": "#e0ac1c"},
        display_name="Михаил Киселёв",
        ig_handle="@m_i_h_0",
    )
    return {"id": cid, "slug": "vauforma", "password": "s3cret-pw"}
