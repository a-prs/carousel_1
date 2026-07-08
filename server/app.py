"""app.py — FastAPI entry for carousel-studio (plan §4).

Single process, single worker (see multi2-carousel-studio.service, BLOCKER B2).
Lifespan runs startup-reconciliation (B1) and starts the in-process job queue.

Security invariants enforced here:
 - tenant always taken from the token, never the request body;
 - every job/download endpoint checks generation.client_id == token.client_id and
   returns 404 (not 403) on mismatch (don't confirm another tenant's job exists);
 - /generate verifies cover_upload_id ownership before creating a generation (B5);
 - slide path built ONLY from the DB job_id + f"slide_{n:02d}.png", n an int
   (no path traversal); jobs/ is NEVER mounted as StaticFiles.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

import auth
import db
import pipeline
from worker import Worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("carousel.app")

API = "/carousel/api"
MAX_UPLOAD = 12 * 1024 * 1024   # 12 MB
MAX_TEXT = 30000
ALLOWED_CT = {"image/jpeg", "image/png", "image/webp"}
COVER_W, COVER_H = 1080, 1350

WORKER: Optional[Worker] = None


def jobs_dir() -> Path:
    return pipeline.jobs_dir()


async def init_state(segmenter: Optional[Callable] = None, renderer: Optional[Callable] = None):
    global WORKER
    await db.apply_schema()
    n = await db.reconcile_startup()
    if n:
        logger.warning("startup reconciliation: %d stale generations -> error", n)
    WORKER = Worker(segmenter=segmenter, renderer=renderer)
    WORKER.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_state()
    yield
    if WORKER:
        await WORKER.stop()


app = FastAPI(title="Carousel Studio", docs_url=None, redoc_url=None, lifespan=lifespan)

BASE_DIR = Path(__file__).parent
SPA_DIR = BASE_DIR / "static-web"

# SPA assets (built by vite). jobs/ is deliberately NOT mounted here.
if (SPA_DIR / "assets").exists():
    app.mount("/carousel/app/assets", StaticFiles(directory=str(SPA_DIR / "assets")), name="spa-assets")

# PWA static files (plan §10 Phase 10). Vite copies web/public/* to the root of
# static-web/ (not under assets/), so each needs its own route registered
# BEFORE the SPA catch-all below — Starlette matches routes in registration
# order, and the catch-all's /{path:path} would otherwise shadow these with
# index.html.
_PWA_FILES = {
    "manifest.webmanifest": "application/manifest+json",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-512-maskable.png": "image/png",
    "apple-touch-icon.png": "image/png",
}


def _register_pwa_routes() -> None:
    for name, media_type in _PWA_FILES.items():
        def handler(name=name, media_type=media_type):
            async def _serve():
                p = SPA_DIR / name
                if not p.exists():
                    raise HTTPException(status_code=404, detail="SPA not built")
                return FileResponse(str(p), media_type=media_type)
            return _serve
        app.add_api_route(f"/carousel/app/{name}", handler(), methods=["GET"])


_register_pwa_routes()


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

@app.post(f"{API}/auth")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    client = await db.get_client_by_slug(username)
    if not client or not client.get("active", 1) or not auth.verify_password(password, client["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = auth.generate_token(client["slug"], client["token_version"])
    resp = JSONResponse({"token": token, "slug": client["slug"]})
    resp.set_cookie(
        auth.COOKIE_NAME, token, max_age=auth.TOKEN_TTL,
        httponly=True, secure=True, samesite="lax", path=auth.COOKIE_PATH,
    )
    return resp


@app.get(f"{API}/me")
async def me(client: auth.AuthedClient = Depends(auth.verify_auth)):
    return {"slug": client.slug, "display_name": client.display_name, "ig_handle": client.ig_handle}


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------

def _process_cover(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)      # normalize EXIF orientation
    img = img.convert("RGB")
    img = ImageOps.fit(img, (COVER_W, COVER_H), method=Image.LANCZOS)  # crop to 4:5
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


@app.post(f"{API}/upload-cover")
async def upload_cover(
    file: UploadFile = File(...),
    client: auth.AuthedClient = Depends(auth.verify_auth),
):
    if file.content_type not in ALLOWED_CT:
        raise HTTPException(status_code=415, detail="Только JPEG, PNG или WebP")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс 12 МБ)")
    try:
        processed = _process_cover(data)
    except Exception:
        raise HTTPException(status_code=400, detail="Не удалось обработать изображение")
    upload_id = uuid.uuid4().hex
    d = jobs_dir() / upload_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "cover.jpg").write_bytes(processed)
    await db.create_pending_upload(upload_id, client.id)
    return {"upload_id": upload_id, "preview_url": f"{API}/uploads/{upload_id}/cover.jpg"}


@app.get(f"{API}/uploads/{{upload_id}}/cover.jpg")
async def upload_preview(upload_id: str, client: auth.AuthedClient = Depends(auth.verify_auth)):
    pu = await db.get_pending_upload(upload_id)
    if not pu or pu["client_id"] != client.id:
        raise HTTPException(status_code=404, detail="Не найдено")
    path = jobs_dir() / upload_id / "cover.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Не найдено")
    return FileResponse(str(path), media_type="image/jpeg")


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------

@app.post(f"{API}/generate")
async def generate(request: Request, client: auth.AuthedClient = Depends(auth.verify_auth)):
    body = await request.json()
    text = (body.get("text") or "").strip()
    cover_upload_id = body.get("cover_upload_id")
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст")
    if len(text) > MAX_TEXT:
        raise HTTPException(status_code=400, detail=f"Текст слишком длинный (макс {MAX_TEXT})")

    cover_path = None
    if cover_upload_id:
        pu = await db.get_pending_upload(cover_upload_id)
        # BLOCKER B5: cover must belong to the calling tenant.
        if not pu or pu["client_id"] != client.id:
            raise HTTPException(status_code=404, detail="Обложка не найдена")
        src = jobs_dir() / cover_upload_id / "cover.jpg"
        if src.exists():
            cover_path = src

    # Simple generate/regenerate distinction (activity_log, plan §10 Phase 9):
    # a tenant with any prior generation is "regenerating", not exact dedup by
    # upload/text — kept intentionally simple per plan guidance.
    prior = await db.list_generations(client.id, limit=1)
    action = "regenerate" if prior else "generate"

    job_id = uuid.uuid4().hex
    job_dir = jobs_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    if cover_path:
        shutil.copyfile(cover_path, job_dir / "cover.jpg")
        cover_path = job_dir / "cover.jpg"
        await db.delete_pending_upload(cover_upload_id)

    await db.create_generation(client.id, job_id)
    await db.log_activity(client.id, action, job_id=job_id)
    # fresh client row (with design_tokens) for the pipeline
    client_row = await db.get_client_by_id(client.id)
    await WORKER.enqueue({"job_id": job_id, "client": client_row, "text": text, "cover_path": cover_path})
    return {"job_id": job_id}


# --------------------------------------------------------------------------
# poll / download
# --------------------------------------------------------------------------

async def _owned_generation(job_id: str, client: auth.AuthedClient) -> dict:
    g = await db.get_generation(job_id)
    if not g or g["client_id"] != client.id:
        raise HTTPException(status_code=404, detail="Не найдено")  # never 403
    return g


@app.get(f"{API}/jobs/{{job_id}}")
async def job_status(job_id: str, client: auth.AuthedClient = Depends(auth.verify_auth)):
    g = await _owned_generation(job_id, client)
    slides = []
    if g["status"] == "done" and not g["expired"]:
        slides = [{"index": i, "url": f"{API}/jobs/{job_id}/slide/{i}.png"}
                  for i in range(1, (g["slide_count"] or 0) + 1)]
    return {
        "status": g["status"], "slide_count": g["slide_count"],
        "cover_title": g["cover_title"], "error": g["error"],
        "expired": bool(g["expired"]), "slides": slides,
    }


@app.get(f"{API}/jobs/{{job_id}}/slide/{{n}}.png")
async def slide_png(job_id: str, n: int, client: auth.AuthedClient = Depends(auth.verify_auth)):
    g = await _owned_generation(job_id, client)
    if g["expired"]:
        raise HTTPException(status_code=410, detail="Файлы удалены по истечении срока хранения")
    if n < 1 or n > (g["slide_count"] or 0):
        raise HTTPException(status_code=404, detail="Слайд не найден")
    # Path built ONLY from validated DB job_id + int n — no raw request string.
    path = jobs_dir() / g["job_id"] / f"slide_{n:02d}.png"
    if not path.exists():
        raise HTTPException(status_code=410, detail="Файл недоступен")
    await db.log_activity(client.id, "download_slide", job_id=job_id, meta={"slide": n})
    return FileResponse(
        str(path), media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="slide_{n:02d}.png"'},
    )


@app.get(f"{API}/jobs/{{job_id}}/zip")
async def job_zip(job_id: str, client: auth.AuthedClient = Depends(auth.verify_auth)):
    g = await _owned_generation(job_id, client)
    if g["expired"]:
        raise HTTPException(status_code=410, detail="Файлы удалены по истечении срока хранения")
    count = g["slide_count"] or 0
    if g["status"] != "done" or count < 1:
        raise HTTPException(status_code=404, detail="Карусель ещё не готова")
    # Same path-safety rule as slide_png: job_dir from the validated DB job_id,
    # filenames from the validated int range only — never from request input.
    job_dir = jobs_dir() / g["job_id"]
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(1, count + 1):
            p = job_dir / f"slide_{i:02d}.png"
            if p.exists():
                zf.write(p, arcname=f"slide_{i:02d}.png")
                added += 1
    if added == 0:
        raise HTTPException(status_code=410, detail="Файлы недоступны")
    buf.seek(0)
    await db.log_activity(client.id, "download_zip", job_id=job_id)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="carousel_{job_id}.zip"'},
    )


@app.get(f"{API}/history")
async def history(client: auth.AuthedClient = Depends(auth.verify_auth)):
    rows = await db.list_generations(client.id, limit=30)
    return {"items": [
        {"job_id": r["job_id"], "created_at": r["created_at"], "cover_title": r["cover_title"],
         "slide_count": r["slide_count"], "status": r["status"], "expired": bool(r["expired"])}
        for r in rows
    ]}


@app.get("/carousel/health")
async def health():
    return {"status": "ok"}


# SPA catch-all (must be last)
@app.get("/carousel/app/{path:path}")
@app.get("/carousel/app")
async def spa(path: str = ""):
    index = SPA_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"error": "SPA not built. Run: cd web && npm run build"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8055")))
