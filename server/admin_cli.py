#!/usr/bin/env python3
"""admin_cli.py — client management CLI (plan §4).

  add-client --slug --handle --display-name --tokens tokens.json --password '...'
  set-theme --slug --tokens tokens.json
  reset-password --slug --password '...'   (bumps token_version -> invalidates old tokens)
  history [--slug SLUG]                    (cross-tenant listing from generations)

NOTE: `history` reads the `generations` table by design (plan §4) — it's the
single "date / client / cover_title / status across ALL tenants" view. Per-action
detail (generate/regenerate/download_slide/download_zip) is logged separately in
`activity_log` (`db.log_activity`, see `server/app.py`) for finer-grained
auditing if ever needed.

`--tokens` takes a JSON file with the design-tokens shape (bg/text/accent/
secondary/fonts — see `server/db.py`); generate one per client with the
`ui-ux-pro-max` skill, or hand-write it for a quick test.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import auth
import db


def _load_tokens(path: str) -> dict:
    return json.loads(Path(path).read_text())


async def cmd_add_client(a):
    tokens = _load_tokens(a.tokens) if a.tokens else {}
    cid = await db.create_client(
        slug=a.slug, password_hash=auth.hash_password(a.password),
        design_tokens=tokens, display_name=a.display_name, ig_handle=a.handle,
    )
    print(f"created client id={cid} slug={a.slug}")


async def cmd_set_theme(a):
    tokens = _load_tokens(a.tokens)
    await db.set_theme(a.slug, tokens)
    print(f"theme updated for {a.slug}")


async def cmd_reset_password(a):
    c = await db.get_client_by_slug(a.slug)
    if not c:
        print(f"no such client: {a.slug}", file=sys.stderr); sys.exit(1)
    await db.set_password(a.slug, auth.hash_password(a.password))
    print(f"password reset for {a.slug} (token_version bumped, old tokens invalidated)")


async def cmd_history(a):
    rows = await db.list_all_generations(slug=a.slug, limit=a.limit)
    if not rows:
        print("(no generations)"); return
    print(f"{'created_at':<20} {'slug':<12} {'status':<8} {'slides':<6} cover_title")
    for r in rows:
        print(f"{(r['created_at'] or ''):<20} {r['slug']:<12} {r['status']:<8} "
              f"{str(r['slide_count'] or 0):<6} {r['cover_title'] or ''}")


def main():
    ap = argparse.ArgumentParser(description="carousel-studio admin")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-client"); p.set_defaults(fn=cmd_add_client)
    p.add_argument("--slug", required=True)
    p.add_argument("--handle", default=None)
    p.add_argument("--display-name", dest="display_name", default=None)
    p.add_argument("--tokens", default=None)
    p.add_argument("--password", required=True)

    p = sub.add_parser("set-theme"); p.set_defaults(fn=cmd_set_theme)
    p.add_argument("--slug", required=True)
    p.add_argument("--tokens", required=True)

    p = sub.add_parser("reset-password"); p.set_defaults(fn=cmd_reset_password)
    p.add_argument("--slug", required=True)
    p.add_argument("--password", required=True)

    p = sub.add_parser("history"); p.set_defaults(fn=cmd_history)
    p.add_argument("--slug", default=None)
    p.add_argument("--limit", type=int, default=50)

    a = ap.parse_args()

    async def _run():
        await db.apply_schema()  # idempotent (CREATE IF NOT EXISTS)
        await a.fn(a)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
