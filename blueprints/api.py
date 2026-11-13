"""Lane C. HTTP API for the viewer.

Run from the repo root:  uv run --extra brain uvicorn blueprints.api:app --reload
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import pipeline
from .schemas import Answer, Scene

app = FastAPI(title="Make Blueprints Talk")


class Ask(BaseModel):
    path: str  # dataset path: "data/swiss_dwellings/x.pdf"
    question: str


def _drawing(path: str):
    try:
        return pipeline.dataset_file(path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except FileNotFoundError:
        raise HTTPException(404, f"no such drawing: {path}") from None


@app.post("/api/ask")
def ask(body: Ask) -> Answer:
    _drawing(body.path)
    return pipeline.answer(body.path, body.question)


@app.get("/api/scene")
def scene(path: str) -> Scene:
    _drawing(path)
    try:
        return pipeline.get_scene(path)
    except NotImplementedError as e:
        raise HTTPException(501, str(e)) from None


@app.get("/api/image")
def image(path: str) -> FileResponse:
    """Serves Page.image: a dataset file ("data/...") or a rendered page ("pages/...")."""
    if path.startswith("data/"):
        return FileResponse(_drawing(path))
    pages = pipeline.CACHE / "pages"
    file = (pipeline.CACHE / path).resolve()
    if not file.is_relative_to(pages) or not file.is_file():
        raise HTTPException(404, f"no such image: {path}")
    return FileResponse(file)
