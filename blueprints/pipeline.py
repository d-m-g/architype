"""Shared glue: the one path every question takes. Lanes fill in the functions this file calls.

Lane modules are imported inside the functions on purpose: a missing package in one lane (ifcopenshell, modal,
openai) must not break the others. Keep heavy imports inside functions in your own modules too.
"""
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from .executor import execute
from .schemas import Answer, Scene

DATA_ROOT = Path(os.environ.get("BLUEPRINTS_DATA", "junction-dataset")).resolve()
CACHE = Path(os.environ.get("BLUEPRINTS_CACHE", ".cache")).resolve()
READ_CACHE = True  # eval.py --fresh turns this off while you iterate on a builder
ESCALATE_BELOW = 0.5  # ponytail: one fixed threshold; tune it on the eval's cost-against-accuracy curve


def dataset_file(path: str) -> Path:
    """Absolute path of a drawing. Paths arrive from the API, so anything outside the dataset is refused."""
    file = (DATA_ROOT / path).resolve()
    if not file.is_relative_to(DATA_ROOT / "data"):
        raise ValueError(f"not a dataset path: {path}")
    if not file.is_file():
        raise FileNotFoundError(path)
    return file


def source_of(path: str) -> str:
    return path.split("/")[1]  # "data/<source>/..."


def build_scene(path: str) -> Scene:
    """Pick the builder for a drawing. Each builder is build_scene(path, file) -> Scene."""
    file, source = dataset_file(path), source_of(path)
    if source == "swiss_dwellings":
        from .parsers import swiss
        return swiss.build_scene(path, file)
    if source in ("ifc_models", "ifc_bench"):
        from .parsers import ifc
        return ifc.build_scene(path, file)
    if source == "cubicasa5k":
        from .detector import raster, rooms
        scene = raster.build_scene(path, file)
        try:
            rooms.add_rooms(scene, file)
        except NotImplementedError:
            pass  # the scene simply does not cover rooms yet
        return scene
    raise NotImplementedError(f"no scene builder for {source}")


@lru_cache(maxsize=16)
def get_scene(path: str) -> Scene:
    """The scene for a drawing, from .cache/scenes/<source>/ when it was built before."""
    dataset_file(path)
    cached = CACHE / "scenes" / source_of(path) / (hashlib.md5(path.encode()).hexdigest() + ".json")
    if READ_CACHE and cached.exists():
        return Scene.model_validate_json(cached.read_text())
    scene = build_scene(path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(scene.model_dump_json())
    return scene


def answer(path: str, question: str) -> Answer:
    scene = None
    try:
        scene = get_scene(path)
        from .brain.plan import make_plan
        result = execute(make_plan(question, scene), scene)
    except NotImplementedError as e:
        result = Answer(error=str(e))
    if result.answer is None or result.confidence < ESCALATE_BELOW:
        try:
            from .brain.escalate import escalate
            result = escalate(question, path, scene, result)
        except NotImplementedError:
            pass
    return result
