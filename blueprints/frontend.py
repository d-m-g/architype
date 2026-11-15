"""Shared browser-asset allowlist for local and deployed frontend mounts."""
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

# Explicit browser asset list: never expose environments, caches, tests or helpers.
PUBLIC = {
    "index.html", "dashboard.html", "styles.css", "dashboard.css", "workspace.css",
    "plan.svg", "app.js", "api-client.js", "contracts.js", "mock-scene.js",
    "dashboard.js", "mock-evaluation.js", "shared-dashboard.js", "workspace.js",
    "editor-bridge.js", "geometry-3d.js", "room-renderer.js", "voice.js", "voice-input.js", "voice-live.js", "voice-pcm-worklet.js",
    "demo.js", "intake.js", "local-file.js", "corrections.js", "correction-state.js", "history.js", "history-state.js", "calibration.js", "calibration-state.js",
    "favicon.svg", "apple-touch-icon.png", "og.png", "robots.txt",
    "app.css", "main.js", "object-question.js", "start.js", "edit.js", "evaluation.js",  # the current page; the files above it are the previous one's
    "vendor/three/build/three.module.js", "vendor/three/build/three.core.js",
    "vendor/three/examples/jsm/controls/OrbitControls.js",
    # the 3D view's fixture models (frontend-d/assets/models/CREDITS.md) and the loader that reads them
    "vendor/three/examples/jsm/loaders/GLTFLoader.js", "vendor/three/examples/jsm/utils/BufferGeometryUtils.js",
    "vendor/three/examples/jsm/utils/SkeletonUtils.js",
    "assets/models/toilet.glb", "assets/models/bathroomSinkSquare.glb", "assets/models/kitchenSink.glb",
    "assets/models/bathtub.glb", "assets/models/shower.glb",
    # eval runs the Evaluation tab preloads (evaluation.js PRELOAD)
    "runs/20261115-041145-pipeline.jsonl", "runs/20261115-040600-plain-gemini-4-carbon.jsonl",
    "runs/20261115-034947-plain-gemini-4-argon.jsonl", "runs/20261115-040157-plain-gemini-4-hydrogen.jsonl",
}

class PublicFrontend(StaticFiles):
    async def get_response(self, path, scope):
        # StaticFiles normalizes nested paths with the host's separator.
        path = path.replace("\\", "/")
        if path in ("", "."):
            path = "index.html"
        if path not in PUBLIC and not (path.startswith("guest/") and path.endswith(".jpg")):  # guest gallery thumbnails
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

