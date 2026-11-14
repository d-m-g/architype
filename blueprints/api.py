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
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import pipeline
from .executor import execute
from .schemas import Answer, QueryPlan, Scene

app = FastAPI(title="Make Blueprints Talk")

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

UPLOAD_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".dxf"}  # DXF goes to lane A's parser; IFC waits for its reader
UPLOAD_MAX_BYTES = 50 * 1024 * 1024


class Ask(BaseModel):
    path: str  # dataset path ("data/swiss_dwellings/x.pdf") or an upload id ("uploads/<id>.pdf")
    question: str


class Recompute(BaseModel):
    scene: Scene  # the scene as the user corrected it in the viewer
    plan: QueryPlan


def _drawing(path: str):
    from .brain.vision import drawing_file
    try:
        return drawing_file(path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except FileNotFoundError:
        raise HTTPException(404, f"no such drawing: {path}") from None


def _parsed(path: str) -> bool:
    """Uploads with a structured parser go through the pipeline like dataset drawings; the rest to the vision reader."""
    return path.lower().endswith(".dxf")


@app.post("/api/ask")
def ask(body: Ask) -> Answer:
    _drawing(body.path)
    if body.path.startswith("uploads/") and not _parsed(body.path):
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
    return execute(body.plan, body.scene)


@app.post("/api/upload")
async def upload(request: Request, filename: str) -> dict:
    """Store an uploaded drawing; send the file as the raw request body. Returns the path to use in /api/ask."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in UPLOAD_TYPES:
        raise HTTPException(415, f"supported uploads: {', '.join(sorted(UPLOAD_TYPES))}")
    data = await request.body()
    if not data or len(data) > UPLOAD_MAX_BYTES:
        raise HTTPException(413, "empty file or larger than 50 MB")
    from .brain.vision import BUILDER, UPLOADS
    name = hashlib.sha256(data).hexdigest()[:20] + suffix  # content-addressed: the user's filename never touches the disk
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / name).write_bytes(data)
    return {"path": f"uploads/{name}", "reader": "swiss_dxf_upload@1" if _parsed(name) else BUILDER}


@app.get("/api/scene")
def scene(path: str) -> Scene:
    _drawing(path)
    try:
        if path.startswith("uploads/") and not _parsed(path):
            from .brain.vision import read_scene
            return read_scene(path)[0]
        return pipeline.get_scene(path)
    except NotImplementedError as e:
        raise HTTPException(501, str(e)) from None


@app.get("/api/image")
def image(path: str) -> FileResponse:
    """Serves Page.image: a dataset file ("data/..."), an upload ("uploads/...") or a rendered page ("pages/...")."""
    if path.startswith(("data/", "uploads/")):
        return FileResponse(_drawing(path))
    pages = pipeline.CACHE / "pages"
    file = (pipeline.CACHE / path).resolve()
    if not file.is_relative_to(pages) or not file.is_file():
        raise HTTPException(404, f"no such image: {path}")
    return FileResponse(file)


# The viewer on the same origin, so the demo is one container. Mounted last: /api/* routes win.
from .frontend import PublicFrontend  # noqa: E402
from pathlib import Path  # noqa: E402

app.mount("/", PublicFrontend(directory=Path(__file__).parent.parent / "frontend-d", html=True), name="viewer")
