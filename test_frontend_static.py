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


def check_oversized_image():
    """/api/image hands the viewer a copy within its limits for an oversized raster, and the file itself otherwise."""
    import tempfile

    import pymupdf

    with tempfile.TemporaryDirectory() as tmp, patch.object(api.pipeline, "CACHE", Path(tmp)):
        gray = pymupdf.csGRAY
        for name, (w, h), dpi in [("big.png", (8000, 5000), 600), ("untagged.png", (8000, 5000), None), ("strip.png", (20000, 300), None)]:
            pix = pymupdf.Pixmap(gray, pymupdf.IRect(0, 0, w, h))
            pix.clear_with(255)
            if dpi:
                pix.set_dpi(dpi, dpi)
            file = Path(tmp) / name
            pix.save(file)
            small = api._viewable("uploads/" + name, file)
            assert small != file and small.parent == Path(tmp) / "pages", name
            out = pymupdf.Pixmap(small)
            assert max(out.width, out.height) <= api.VIEW_MAX_SIDE and out.width * out.height <= api.VIEW_MAX_PIXELS, (name, out.width, out.height)
            assert abs(out.width / out.height - w / h) <= 0.02 * w / h, (name, out.width, out.height)
            assert max(out.width / w, out.height / h) > 0.5, (name, out.width, out.height)  # shrunk only as far as needed
            assert api._viewable("uploads/" + name, file) == small  # made once
        fits = Path(tmp) / "fits.png"
        pymupdf.Pixmap(gray, pymupdf.IRect(0, 0, 2480, 1753)).save(fits)
        assert api._viewable("uploads/fits.png", fits) == fits
        notes = Path(tmp) / "notes.pdf"
        notes.write_bytes(b"%PDF-1.7\n")
        assert api._viewable("uploads/notes.pdf", notes) == notes


if __name__ == "__main__":
    asyncio.run(check_frontend())
    check_oversized_image()
    print("ok: public assets, private-file rejection, sign-in, API routing and oversized images")
