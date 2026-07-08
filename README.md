# carousel-studio

Multitenant Instagram-carousel generator. Client logs in on a phone, uploads a
cover photo + raw post text, hits a button, gets per-slide PNGs (1080×1350) in
their brand design system. LLM (`claude -p`, Max subscription) segments the text
into typed cards → per-tenant Jinja2 theme → Playwright screenshots each slide.

Tenants, passwords (bcrypt) and `design_tokens` live in SQLite, not code — a new
client is a DB row + a design-tokens pass, no deploy. **For agents onboarding a
new tenant:** generate the client's initial palette/font-pairing with the
`ui-ux-pro-max` Claude Code skill (color/typography presets by product type)
before writing the tokens with `admin_cli.py add-client --tokens ...` — see
"admin_cli" below.

## Status: Phase 0–13 complete. See `data/projects/carousel-studio/PLAN.md` (internal).

Implemented: skeleton, DB, auth (HMAC multitenant + bcrypt + token_version + 7d
cookie), templates + render (self-hosted cyrillic woff2, honest `fonts.ready`,
overflow-guard), LLM segment/validate/repair (typed errors, no SystemExit), the
asyncio worker + pipeline (conc=1, per-job isolation, startup reconciliation),
the API (auth isolation, B5 upload ownership, path-traversal-safe slide download,
zip download, `activity_log` on generate/regenerate/download_slide/download_zip),
the React/TS/Vite SPA (localStorage token, session restore, Web Share save, zip
button on the result screen), a PWA manifest + icons (192/512/maskable/apple-touch,
no service worker), `retention.py` (TTL 72h file cleanup + `expired` flag, never
touches `generations`/`activity_log` content, short per-job transactions so one
locked row or a missing file never aborts the run), a real
login→upload→generate→render→slide-download→zip-download→history E2E (desktop +
emulated mobile viewport, Web Share mocked), and production deploy (systemd
service + retention timer + nginx/TLS reverse proxy, see "Deploy" below).

**Open item, real device required:** Web Share "Save to Photos" is only verified
as a *code path* (mocked `navigator.share`/`canShare` in headless Playwright,
see `tests/_mobile_e2e.py`). Whether it actually lands in iOS Photos can only be
confirmed on a real iPhone.

## HARD INVARIANTS (do not break)

- **Single process, single worker**: `uvicorn app:app --workers 1`. The in-memory
  job queue and the Playwright concurrency-1 lock live in one process. Never
  gunicorn-prefork or `--workers N`.
- `data/carousel-studio/jobs/` is **never** mounted as StaticFiles — downloads go
  only through ownership-checked endpoints.
- Secrets: `.env` and any generated `*_credentials.txt` are git-ignored. Never
  commit them, and never commit real domains/IPs into `deploy/nginx.conf.example`
  — copy it, fill in your own, keep the filled-in copy out of git.

## Required components

- **Python 3.11+** with the packages in `requirements.txt` (FastAPI, uvicorn,
  aiosqlite, jinja2, passlib+bcrypt, playwright, pillow).
- **Playwright's Chromium** installed once: `.venv/bin/playwright install chromium`
  (used to screenshot each rendered slide).
- **Node/npm** to build the SPA once (`web/` — React + Vite + TS); the built
  output is served as static files by the FastAPI app, not by Node at runtime.
- **`claude` CLI installed and logged into a Claude subscription** on the host
  (server/llm.py shells out to `claude -p` for text segmentation — no
  `ANTHROPIC_API_KEY` / API billing involved).
- **SQLite** — no external DB server; the schema is created automatically on
  first run at `$CAROUSEL_DATA_DIR`.
- Optional for production: **systemd** (unit files in the repo root) and
  **nginx + certbot** (`deploy/nginx.conf.example`) for TLS + a real domain.

## Environment variables

Copy this into a `.env` file in the repo root (git-ignored) and fill in:

```
# Random secret used to sign auth tokens (HMAC). Generate with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
CAROUSEL_TOKEN_SECRET=

# Server port (uvicorn binds 127.0.0.1:$PORT — put a reverse proxy in front
# for TLS/public access, see deploy/nginx.conf.example).
PORT=8055

# Model used for the `claude -p` text-segmentation call (server/llm.py).
MODEL=claude-sonnet-5

# Where Playwright's Chromium lives. Depends on your OS/user; find it with
# `.venv/bin/playwright install chromium` (it prints the path) if unsure.
PLAYWRIGHT_BROWSERS_PATH=/home/YOUR_USER/.cache/ms-playwright

# Where the SQLite DB + per-job render files live. Must be writable by the
# service user; keep it OUTSIDE the app's own git checkout.
CAROUSEL_DATA_DIR=/path/to/data/carousel-studio

# Optional — hours before a job's render files are deleted by
# server/retention.py (DB history/activity_log is never touched).
CAROUSEL_RETENTION_TTL_HOURS=72
```

## Run (dev)

```bash
cd carousel-studio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
# write .env (see "Environment variables" above), then:
# seed a client
.venv/bin/python server/admin_cli.py add-client --slug demo --password 'change-me'
# build SPA (once)
cd web && npm install && npm run build && cd ..
# run
set -a; source .env; set +a
.venv/bin/uvicorn app:app --app-dir server --host 127.0.0.1 --port 8055 --workers 1
# open http://127.0.0.1:8055/carousel/app/
```

Tests: `.venv/bin/python -m pytest -q` (74 tests). Regenerate fonts:
`.venv/bin/python tests/_fetch_fonts.py`. Mobile E2E helper: `tests/_mobile_e2e.py`.

Retention, run by hand (dry-run friendly — only touches dirs older than the TTL):
`CAROUSEL_DATA_DIR=... .venv/bin/python server/retention.py` (defaults to the
real `data/carousel-studio` dir if `CAROUSEL_DATA_DIR` is unset — the systemd
service pins `WorkingDirectory=server` and `EnvironmentFile=.env`, see
`multi2-carousel-retention.service`).

## admin_cli

`add-client --slug --handle --display-name --tokens tokens.json --password '...'`,
`set-theme --slug --tokens tokens.json`, `reset-password --slug --password '...'`
(bumps token_version → kills old tokens), `history [--slug]` (cross-tenant
listing straight from `generations`, intentional — not a placeholder).

`--tokens` takes a JSON file of design tokens (bg/text/accent/secondary/fonts —
see `server/db.py` for the full shape). Generate it per-client with the
`ui-ux-pro-max` skill, or hand-write one for a quick test.

## Deploy

1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`,
   `.venv/bin/playwright install chromium`, build the SPA (`web/`, see "Run" above).
2. Write `.env` (see "Environment variables" above) at the repo root, and seed
   at least one client with `admin_cli.py add-client`.
3. Edit the paths in `multi2-carousel-studio.service` and
   `multi2-carousel-retention.{service,timer}` to point at your checkout, then:
   ```
   sudo cp multi2-carousel-studio.service multi2-carousel-retention.service \
       multi2-carousel-retention.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now multi2-carousel-studio.service
   sudo systemctl enable --now multi2-carousel-retention.timer
   ```
4. Copy `deploy/nginx.conf.example`, fill in your own domain, install it, then
   `certbot --nginx -d YOUR_DOMAIN` for TLS (see comments in that file).
5. Re-run the Playwright E2E against the real domain (desktop + mobile viewport).
6. **Real-iOS acceptance check**: open the deployed PWA on an actual iPhone,
   confirm "Save" on a slide really lands in Photos (Web Share API) — this has
   only been verified as a code path in headless Playwright so far.
