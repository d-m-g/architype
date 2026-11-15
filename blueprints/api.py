"""Lane C. HTTP API for the viewer.

Run from the repo root:  uv run --env-file .env --extra brain uvicorn blueprints.api:app --reload

Login (the same pattern as work-tracker): set BLUEPRINTS_PASSWORD_HASH to a hash from
`python -m blueprints.api hash` and every request needs a signed session cookie. A browser without one gets the
sign-in page in place of the app, /api/* gets a 401, and POST /api/login {"password"} hands out the cookie.
With no hash set the server runs open, as it always has (local development, eval).
"""
import hashlib
import hmac
import os
import secrets
import sys
import threading
import time
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import pipeline
from .executor import execute
from .schemas import Answer, QueryPlan, Scene

app = FastAPI(title="Make Blueprints Talk")


@app.get("/api/health")
def health() -> dict:
    """Read-only deployment readiness; the usual sign-in middleware still applies."""
    from .brain.room_link_vision import VERSION, VALIDATION_VERSION
    return {"status": "ok", "room_link_refiner": VERSION, "room_link_validator": VALIDATION_VERSION}

PASSWORD_HASH = os.environ.get("BLUEPRINTS_PASSWORD_HASH")  # pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>
COOKIE = "blueprints_session"
SESSION_SECONDS = 7 * 24 * 3600
COOKIE_SECURE = os.environ.get("BLUEPRINTS_COOKIE_INSECURE") != "1"  # set to 1 only to test login over plain http
_failures: dict[str, list[float]] = {}  # client -> recent failed logins; ponytail: in memory, resets on restart


def hash_password(password: str, iterations: int = 300_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


if __name__ == "__main__" and sys.argv[1:] == ["hash"]:  # python -m blueprints.api hash  (reads the password from stdin)
    print(hash_password(sys.stdin.readline().rstrip("\n")))
    sys.exit(0)


def _password_ok(password: str) -> bool:
    try:
        _, iterations, salt, expected = PASSWORD_HASH.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(got, bytes.fromhex(expected))
    except (AttributeError, ValueError):
        return False


def _secret() -> bytes:
    """The key that signs session cookies, kept beside the caches so sessions survive a restart."""
    path = pipeline.CACHE / ".session_secret"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


def _token(expires: int) -> str:
    return f"{expires}.{hmac.new(_secret(), str(expires).encode(), hashlib.sha256).hexdigest()}"


def _signed_in(request: Request) -> bool:
    expires, _, _sig = request.cookies.get(COOKIE, "").partition(".")
    return expires.isdigit() and int(expires) > time.time() and hmac.compare_digest(_token(int(expires)), request.cookies[COOKIE])


def _client(request: Request) -> str:
    # Traefik appends the caller's address last; anything before it the caller could have written.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "?")


@app.middleware("http")
async def _require_login(request: Request, call_next):
    if not PASSWORD_HASH or request.url.path in ("/api/login", "/api/logout") or _signed_in(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "sign in first"}, status_code=401)
    return HTMLResponse(_LOGIN_PAGE, headers={"Cache-Control": "no-store"})


@app.post("/api/login")
async def login(request: Request) -> JSONResponse:
    client, now = _client(request), time.time()
    recent = [t for t in _failures.get(client, []) if now - t < 300]
    if len(recent) >= 8:
        return JSONResponse({"error": "too many attempts; wait a few minutes and try again"}, status_code=429)
    try:
        password = str((await request.json()).get("password", ""))
    except ValueError:
        password = ""
    if not PASSWORD_HASH or not _password_ok(password):
        _failures[client] = recent + [now]
        return JSONResponse({"error": "wrong password"}, status_code=401)
    _failures.pop(client, None)
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, _token(int(now) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                        httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, httponly=True, secure=COOKIE_SECURE, samesite="lax")
    return response


_LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Make Blueprints Talk: sign in</title>
<style>
 :root{color-scheme:light dark;--page:#f9f9f7;--card:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--rule:rgba(11,11,11,.12);--accent:#2a78d6;--bad:#d03b3b}
 @media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--card:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;--rule:rgba(255,255,255,.12);--accent:#3987e5}}
 *{box-sizing:border-box} body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--page);color:var(--ink);
   font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
 form{width:100%;max-width:340px;background:var(--card);border:1px solid var(--rule);border-radius:14px;padding:28px}
 h1{font-size:20px;margin:0 0 4px} p{margin:0 0 18px;color:var(--ink-2);font-size:14px}
 input{width:100%;min-height:44px;padding:.5rem .7rem;font:inherit;color:inherit;background:transparent;border:1px solid var(--rule);border-radius:8px}
 button{width:100%;min-height:44px;margin-top:12px;font:inherit;font-weight:600;color:#fff;background:var(--accent);border:0;border-radius:8px;cursor:pointer}
 .err{color:var(--bad);font-size:14px;min-height:1.5em;margin:10px 0 0}
</style></head><body>
<form id="f"><h1>Make Blueprints Talk</h1><p>Enter the password to continue.</p>
 <input id="pw" type="password" placeholder="Password" autocomplete="current-password" autofocus required>
 <button>Sign in</button><div class="err" id="err" role="alert"></div></form>
<script>
 document.getElementById('f').addEventListener('submit', async e => {
   e.preventDefault();
   const err = document.getElementById('err'); err.textContent = '';
   const r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
     body: JSON.stringify({password: document.getElementById('pw').value})});
   if (r.ok) location.reload(); else err.textContent = (await r.json().catch(() => ({}))).error || 'sign in failed';
 });
</script></body></html>"""

UPLOAD_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".dxf", ".ifc"}  # DXF, IFC and vector PDF go to lane A's parsers
UPLOAD_MAX_BYTES = 50 * 1024 * 1024
# IFC models are text and large: the dataset's go up to 343 MB, the ones it asks questions about up to 85 MB.
# The 85 MB one builds in ~160 s at 1.7 GB peak; the viewer waits 300 s for /api/scene, so 100 MB leaves headroom.
IFC_MAX_BYTES = 100 * 1024 * 1024


class Ask(BaseModel):
    path: str  # dataset path ("data/swiss_dwellings/x.pdf") or an upload id ("uploads/<id>.pdf")
    question: str


class Recompute(BaseModel):
    scene: Scene  # the scene as the user corrected it in the viewer
    plan: QueryPlan


class RankScene(BaseModel):
    scene: Scene
    question: str


Coord = Annotated[float, Field(ge=0, le=1)]
Box = tuple[Coord, Coord, Coord, Coord]  # x0, y0, x1, y1 of the page, as in Entity.geom


class Alike(BaseModel):
    image: str  # Page.image of the page the boxes are on
    box: Box  # the detection the user rejected
    others: dict[str, Box] = Field(max_length=2000)  # entity id -> box of the detections to compare with it


class Label(BaseModel):
    action: Literal["added", "removed", "resized", "confirmed"]
    image: str  # Page.image of the page the box is on
    entity: str = Field(max_length=80)  # Entity.id
    type: str
    subtype: str | None = None
    box: Box
    was: Box | None = None  # resized: the box before
    conf: float | None = Field(None, ge=0, le=1)  # the detector's score before the correction


class Labels(BaseModel):
    drawing: str  # the drawing's path, as in /api/ask; recording it again replaces its earlier labels
    labels: list[Label] = Field(max_length=2000)  # how the drawing now differs from what the reader found


class Reject(BaseModel):
    image: str  # Page.image of the page the box is on
    box: Box  # the detection the user removed together with its look-alikes
    type: str  # what the detector took it for: Entity.type and subtype
    subtype: str | None = None


def _drawing(path: str):
    from .brain.vision import drawing_file
    try:
        return drawing_file(path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except FileNotFoundError:
        raise HTTPException(404, f"no such drawing: {path}") from None


def _vision_only(path: str) -> bool:
    """Uploads only the vision reader can read. DXF uploads are parsed and PNG/JPEG uploads detected through the
    pipeline (which escalates to the vision reader and agent when it cannot answer); a PDF goes through the pipeline
    when the vector parser recognises it, and to the vision reader otherwise (a scan, a section, a document)."""
    if not path.startswith("uploads/") or path.lower().endswith((".dxf", ".ifc", ".png", ".jpg", ".jpeg")):
        return False
    if not path.lower().endswith(".pdf"):
        return True
    try:
        pipeline.get_scene(path)  # built once and cached
        return False
    except NotImplementedError:
        return True


@app.post("/api/ask")
def ask(body: Ask) -> Answer:
    _drawing(body.path)
    if _vision_only(body.path):
        from .brain.escalate import answer_from_vision
        try:
            return answer_from_vision(body.question, body.path)
        except NotImplementedError as e:
            return Answer(error=str(e))
    return pipeline.answer(body.path, body.question)


@app.get("/api/plan")
def plan(question: str) -> QueryPlan:
    """The plan for a question, so the viewer can rerun it on a corrected scene with /api/recompute."""
    from .brain.plan import make_plan
    try:
        return make_plan(question, None)
    except NotImplementedError as e:
        raise HTTPException(501, str(e)) from None


@app.post("/api/recompute")
def recompute(body: Recompute) -> Answer:
    """Run a plan on a scene the user edited: no model call, the same executor as every other answer."""
    from .brain.vision import checked
    return checked(execute(body.plan, body.scene), body.plan, body.scene)  # as pipeline.answer: no confident zero from the vision reader's rooms


@app.post("/api/room-ranking")
def room_ranking(body: RankScene) -> Answer:
    """Free ranking on the browser's corrected scene; never substitute the original drawing."""
    from .room_ranking import rank, requested
    intent = requested(body.question)
    result = rank(body.scene, *intent) if intent else None
    if result is None:
        raise HTTPException(422, "Not a supported whole-drawing ranking; use the planner.")
    return result


@app.post("/api/upload")
async def upload(request: Request, filename: str) -> dict:
    """Store an uploaded drawing; send the file as the raw request body. Returns the path to use in /api/ask."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in UPLOAD_TYPES:
        raise HTTPException(415, f"supported uploads: {', '.join(sorted(UPLOAD_TYPES))}")
    limit = IFC_MAX_BYTES if suffix == ".ifc" else UPLOAD_MAX_BYTES
    data = await request.body()
    if not data or len(data) > limit:
        raise HTTPException(413, f"empty file or larger than {limit // 2**20} MB")
    from .brain.vision import BUILDER, UPLOADS
    name = hashlib.sha256(data).hexdigest()[:20] + suffix  # content-addressed: the user's filename never touches the disk
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / name).write_bytes(data)
    from .parsers.ifc import BUILDER as IFC_BUILDER
    from .parsers.pdf import BUILDER as PDF_BUILDER
    from .parsers.swiss import UPLOAD_BUILDER as DXF_BUILDER
    path = f"uploads/{name}"
    if suffix in (".png", ".jpg", ".jpeg") or _vision_only(path):
        # An image or a PDF the vector parser does not recognise: check it is a floor plan before anything counts on
        # it. The vision read is the one the drawing gets anyway (rooms, or the whole scene), so this moves it earlier
        # and caches it; it adds no model call. Without the reader (no key) the file is let through, as before.
        from .brain.vision import not_a_floor_plan, read_scene
        try:
            kind = not_a_floor_plan(read_scene(path)[0])
        except NotImplementedError:
            kind = None
        if kind:
            (UPLOADS / name).unlink(missing_ok=True)
            raise HTTPException(422, f"Not a floor plan: this looks like a {kind}. Upload a floor plan (PDF, PNG, JPEG, DXF or IFC).")
    reader = (BUILDER if _vision_only(path) else DXF_BUILDER if suffix == ".dxf" else IFC_BUILDER if suffix == ".ifc"
              else "fixtures-6:tiled" if suffix in (".png", ".jpg", ".jpeg") else PDF_BUILDER)
    return {"path": path, "reader": reader}


@app.get("/api/scene")
def scene(path: str) -> Scene:
    _drawing(path)
    try:
        if _vision_only(path):
            from .brain.vision import read_scene
            return read_scene(path)[0]
        return pipeline.get_scene(path)
    except NotImplementedError as e:
        raise HTTPException(501, str(e)) from None


VIEW_MAX_PIXELS, VIEW_MAX_SIDE = 32_000_000, 16_384  # what the viewer accepts: validateImageSize in frontend-d/local-file.js
_view_lock = threading.Lock()  # ponytail: one lock for every scaled copy; per-file locks if many oversized drawings open at once


@lru_cache(maxsize=4096)
def _pixels(file) -> tuple[int, int]:
    """Pixel size of a raster from its header, (0, 0) when it cannot be read. Dataset files and uploads never change."""
    import pymupdf
    try:
        info = pymupdf.open(file)[0].get_image_info()[0]
        return info["width"], info["height"]
    except Exception:  # an unreadable image is served as it is, as before; the browser reports it
        return 0, 0


def _viewable(path: str, file):
    """The file itself, or for a raster above the viewer's limits a copy scaled down to fit them, made once.
    Entity geometry is relative to the page, so the overlays sit the same on either."""
    if file.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return file
    w, h = _pixels(file)
    scale = min(1.0, VIEW_MAX_SIDE / max(w, h, 1), (VIEW_MAX_PIXELS / max(w * h, 1)) ** 0.5)
    if scale == 1.0:
        return file
    small = pipeline.CACHE / "pages" / f"{hashlib.md5(path.encode()).hexdigest()}-view.png"
    with _view_lock:
        if not small.is_file():
            import pymupdf
            page = pymupdf.open(file)[0]
            # The page rect is in points and follows the image's DPI tag, so scale from it to the pixel size wanted.
            matrix = pymupdf.Matrix(int(w * scale) / page.rect.width, int(h * scale) / page.rect.height)
            small.parent.mkdir(parents=True, exist_ok=True)
            part = small.with_suffix(".part")
            part.write_bytes(page.get_pixmap(matrix=matrix).tobytes("png"))
            part.replace(small)  # a request arriving mid-write must not be served half a file
    return small


def _page_file(path: str):
    """The file behind a Page.image: a dataset file ("data/..."), an upload ("uploads/...") or a rendered page ("pages/...")."""
    if path.startswith(("data/", "uploads/")):
        return _drawing(path)
    file = (pipeline.CACHE / path).resolve()
    if not file.is_relative_to(pipeline.CACHE / "pages") or not file.is_file():
        raise HTTPException(404, f"no such image: {path}")
    return file


@app.get("/api/image")
def image(path: str) -> FileResponse:
    """Serves Page.image. A raster above the viewer's size limits is served as a scaled-down PNG."""
    file = _page_file(path)
    return FileResponse(_viewable(path, file) if path.startswith(("data/", "uploads/")) else file)


@app.get("/api/model")
def model(path: str) -> FileResponse:
    """An IFC model's own 3D geometry as a GLB, for the 3D view. Built on first request and cached."""
    file = _drawing(path)
    if not (path.lower().endswith(".ifc") or pipeline.source_of(path) in ("ifc_models", "ifc_bench")):
        raise HTTPException(404, "only IFC models have 3D geometry")
    from .parsers import ifc
    try:
        return FileResponse(ifc.model_glb(path, file), media_type="model/gltf-binary")
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(422, str(e)) from None


@lru_cache(maxsize=2)  # ponytail: two pages held in memory (a 140 MP sheet is 140 MB); shrink them first if that hurts
def _gray(image: str):
    from PIL import Image
    from .detector.alike import gray
    try:
        return gray(_page_file(image))
    except (OSError, Image.DecompressionBombError):  # a page that is a PDF, not a raster, or too large to open
        raise HTTPException(415, "look-alikes need a raster page image") from None


@app.post("/api/alike")
def alike(body: Alike) -> dict:
    """The ids among `others` that show the same symbol as `box`, most alike first: the user rejects one detection
    and the viewer offers to remove the rest. Compares the page's own pixels; no model call."""
    from .detector.alike import look_alikes
    return {"ids": look_alikes(_gray(body.image), body.box, body.others)}


def _kind(type: str, subtype: str | None) -> str:
    """"door" or "fixture:toilet", refused unless the schema knows it: it names a folder in the cache."""
    from .schemas import Entity
    try:
        Entity(id="_", type=type, subtype=subtype)
    except ValueError:
        raise HTTPException(422, "unknown entity type or subtype") from None
    return f"{type}:{subtype}" if subtype else type


@app.post("/api/labels")
def labels(body: Labels) -> dict:
    """Record the user's corrections on a drawing as training labels (detector/labels.py). Sent when the user
    presses "Record for training" in the viewer, never on its own; an empty list clears the drawing's labels."""
    from .detector import labels as log
    _drawing(body.drawing)
    for image in {label.image for label in body.labels}:
        _page_file(image)  # 404 unless it is a drawing or page this server holds
    for kind in {(label.type, label.subtype) for label in body.labels}:
        _kind(*kind)
    log.save(body.drawing, [label.model_dump(exclude_none=True) for label in body.labels])
    return {"saved": len(body.labels)}


@app.post("/api/reject")
def reject(body: Reject) -> dict:
    """Remember the symbol in `box` as "not a <type>": the detector's boxes on it are set aside on drawings uploaded
    from now on (detector/alike.py). id is null when a remembered symbol already covers it."""
    from .detector.alike import remember
    return {"id": remember(_gray(body.image), body.box, _kind(body.type, body.subtype))}


@app.delete("/api/reject")
def unreject(type: str, id: Annotated[str, Query(pattern="^[0-9a-f]{16}$")], subtype: str | None = None) -> dict:
    """Forget a remembered symbol: the viewer's Undo of the removal that stored it."""
    from .detector.alike import forget
    forget(_kind(type, subtype), id)
    return {"ok": True}


from .brain.voice import router as voice_router
app.include_router(voice_router)

# The viewer on the same origin, so the demo is one container. Mounted last: /api/* routes win.
from .frontend import PublicFrontend  # noqa: E402
from pathlib import Path  # noqa: E402

app.mount("/", PublicFrontend(directory=Path(__file__).parent.parent / "frontend-d", html=True), name="viewer")
