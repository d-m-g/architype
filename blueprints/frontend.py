"""Shared browser-asset allowlist for local and deployed frontend mounts."""
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

# Explicit browser asset list: never expose environments, caches, tests or helpers.
PUBLIC = {
    "index.html", "dashboard.html", "styles.css", "dashboard.css", "workspace.css",
    "plan.svg", "app.js", "api-client.js", "contracts.js", "mock-scene.js",
    "dashboard.js", "mock-evaluation.js", "shared-dashboard.js", "workspace.js",
    "editor-bridge.js", "geometry-3d.js", "room-renderer.js", "voice.js",
    "demo.js", "intake.js", "local-file.js", "corrections.js", "correction-state.js", "history.js", "history-state.js", "calibration.js", "calibration-state.js", "floor-stack.js", "floor-stack-state.js",
    "vendor/three/build/three.module.js", "vendor/three/build/three.core.js",
    "vendor/three/examples/jsm/controls/OrbitControls.js",
}

class PublicFrontend(StaticFiles):
    async def get_response(self, path, scope):
        # StaticFiles normalizes nested paths with the host's separator.
        path = path.replace("\\", "/")
        if path in ("", "."):
            path = "index.html"
        if path not in PUBLIC:
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

