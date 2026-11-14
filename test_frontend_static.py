"""Regression checks for the production frontend boundary (no dataset or API keys).

Run with: uv run --extra brain --with httpx python test_frontend_static.py
"""
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch

import httpx

from blueprints import api
from blueprints.frontend import PUBLIC, PublicFrontend


async def check_frontend():
    # Exercise the API's production mount when present. Main's local entry point
    # mounts the same class; do not let a later safe mount hide an unsafe first one.
    mounts = [route for route in api.app.routes if getattr(route, "path", None) == ""]
    if not mounts:
        spec = importlib.util.spec_from_file_location("frontend_serve", Path(__file__).parent / "frontend-d" / "serve.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mounts = [route for route in api.app.routes if getattr(route, "path", None) == ""]
    assert mounts and all(isinstance(route.app, PublicFrontend) for route in mounts)

    # Real signed session: blocked files must stay blocked after authentication.
    with patch.object(api, "PASSWORD_HASH", api.hash_password("test-only-password")), patch.object(api, "_secret", return_value=b"test-only-session-secret"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="https://testserver") as client:
            assert (await client.get("/api/plan")).status_code == 401
            assert "Enter the password" in (await client.get("/")).text
            response = await client.post("/api/login", json={"password": "test-only-password"})
            assert response.status_code == 200 and api.COOKIE in client.cookies

            for asset in sorted(PUBLIC):
                response = await client.get("/" + asset)
                assert response.status_code == 200, (asset, response.status_code)
                assert response.headers["x-content-type-options"] == "nosniff"
            assert (await client.get("/")).status_code == 200
            assert (await client.head("/app.js")).status_code == 200
            for private in (
                "README.md", "PRIORITIES.md", "start-api.ps1", "serve.py", "package.json",
                "viewer.test.js", "dev-server.js", "fixtures/floor-stack.scene.json",
                "vendor/three/package.json", ".env", "%2eenv", "vendor/three/../../README.md",
            ):
                for method in ("GET", "HEAD"):
                    response = await client.request(method, "/" + private)
                    assert response.status_code == 404, (method, private, response.status_code)
            assert (await client.get("/api/plan")).status_code == 422
            assert (await client.post("/api/logout")).status_code == 200
            assert "Enter the password" in (await client.get("/")).text


if __name__ == "__main__":
    asyncio.run(check_frontend())
    print("ok: public assets, private-file rejection, sign-in and API routing")
