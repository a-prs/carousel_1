"""Phase 10 — PWA manifest/icons smoke tests: valid JSON, required fields,
referenced icon files exist, and are served with the correct Content-Type
through explicit FastAPI routes (not swallowed by the SPA catch-all, which
would otherwise return index.html for any unmatched /carousel/app/* path)."""
import json
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB_PUBLIC = ROOT / "web" / "public"
STATIC_WEB = ROOT / "server" / "static-web"


def test_manifest_source_is_valid_json_with_required_fields():
    data = json.loads((WEB_PUBLIC / "manifest.webmanifest").read_text())
    assert data["name"]
    assert data["short_name"]
    assert data["start_url"] == "/carousel/app"
    assert data["display"] == "standalone"
    assert data["theme_color"]
    assert data["background_color"]
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in data["icons"])
    for icon in data["icons"]:
        assert icon["src"].startswith("/carousel/app/")


def test_web_public_icons_exist_and_are_nonempty():
    for name in ("icon-192.png", "icon-512.png", "icon-512-maskable.png", "apple-touch-icon.png"):
        p = WEB_PUBLIC / name
        assert p.exists() and p.stat().st_size > 0, name


def test_index_html_references_manifest_and_apple_touch_icon():
    html = (ROOT / "web" / "index.html").read_text()
    assert 'rel="manifest"' in html
    assert 'rel="apple-touch-icon"' in html
    assert "viewport-fit=cover" in html
    assert 'name="apple-mobile-web-app-capable"' in html
    assert 'name="theme-color"' in html
    # no service worker required by the plan (§10) — make sure nobody added one
    assert "serviceWorker" not in html


@pytest.mark.skipif(
    not (STATIC_WEB / "manifest.webmanifest").exists(),
    reason="SPA not built (run: cd web && npm run build)",
)
async def test_manifest_and_icons_served_with_correct_content_type():
    import app as app_module
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/carousel/app/manifest.webmanifest")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/manifest+json")
        data = r.json()
        assert data["start_url"] == "/carousel/app"

        icon = await c.get("/carousel/app/icon-192.png")
        assert icon.status_code == 200
        assert icon.headers["content-type"] == "image/png"

        apple = await c.get("/carousel/app/apple-touch-icon.png")
        assert apple.status_code == 200
        assert apple.headers["content-type"] == "image/png"

        # A random unmatched path under /carousel/app/ must still fall back to
        # the SPA (index.html), proving the PWA routes didn't break routing.
        spa = await c.get("/carousel/app/some-client-route")
        assert spa.status_code == 200
        assert "<div id=\"root\">" in spa.text
