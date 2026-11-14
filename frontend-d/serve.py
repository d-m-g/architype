"""D hosting entry point: serve the existing team API and public UI on one origin.

Run from the repo root:
  python -m uvicorn serve:app --app-dir frontend-d --host 127.0.0.1 --port 8000
No team-owned Python source is modified.
"""
from pathlib import Path

from fastapi import Request

from blueprints.api import app
from blueprints.frontend import PublicFrontend

ROOT = Path(__file__).resolve().parent


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
