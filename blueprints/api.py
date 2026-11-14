"""Lane C. HTTP API for the viewer.

Run from the repo root:  uv run --env-file .env --extra brain uvicorn blueprints.api:app --reload
"""
import hashlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import pipeline
from .executor import execute
from .schemas import Answer, QueryPlan, Scene

app = FastAPI(title="Make Blueprints Talk")

UPLOAD_TYPES = {".pdf", ".png", ".jpg", ".jpeg"}  # IFC and DXF uploads wait for lane A's readers
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


@app.post("/api/ask")
def ask(body: Ask) -> Answer:
    _drawing(body.path)
    if body.path.startswith("uploads/"):
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
    return {"path": f"uploads/{name}", "reader": BUILDER}


@app.get("/api/scene")
def scene(path: str) -> Scene:
    _drawing(path)
    try:
        if path.startswith("uploads/"):
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
