"""D hosting entry point: serve the existing team API and public UI on one origin.

Run from the repo root:
  python -m uvicorn serve:app --app-dir frontend-d --host 127.0.0.1 --port 8000
No team-owned Python source is modified.
"""
from pathlib import Path

from fastapi import Request
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from blueprints.api import app

ROOT = Path(__file__).resolve().parent
# Explicit browser asset list: never expose environments, caches, tests or helpers.
PUBLIC = {
    "index.html", "dashboard.html", "styles.css", "dashboard.css", "workspace.css",
    "plan.svg", "app.js", "api-client.js", "contracts.js", "mock-scene.js",
    "dashboard.js", "mock-evaluation.js", "shared-dashboard.js", "workspace.js",
    "editor-bridge.js", "geometry-3d.js", "room-renderer.js", "voice.js",
    "demo.js", "intake.js", "local-file.js", "corrections.js", "correction-state.js", "history.js", "history-state.js", "calibration.js", "calibration-state.js",
    "vendor/three/build/three.module.js", "vendor/three/build/three.core.js",
    "vendor/three/examples/jsm/controls/OrbitControls.js",
}

class PublicFrontend(StaticFiles):
    async def get_response(self, path, scope):
        if path in ("", "."):
            path = "index.html"
        if path not in PUBLIC:
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

@app.get("/__frontend/api-status", include_in_schema=False)
def frontend_status(request: Request):
    paths = {getattr(route, "path", "") for route in app.routes}
    ready = {"/api/ask", "/api/scene", "/api/image"}.issubset(paths)
    return {
        "connected": ready,
        "origin": str(request.base_url).rstrip("/"),
        "title": app.title,
        "message": "UI and team API share this server. Drawing readers are checked per request.",
        "upload_available": "/api/upload" in paths,
        "recompute_available": "/api/recompute" in paths,
    }

# Register last so /api routes, OpenAPI and documentation retain priority.
app.mount("/", PublicFrontend(directory=ROOT, html=True), name="d-frontend")
