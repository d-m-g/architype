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
_BUILT_NOW: set[str] = set()  # scenes built in this process; the next answer on that drawing pays their cost


def dataset_file(path: str) -> Path:
    """Absolute path of a drawing. Paths arrive from the API, so anything outside the dataset is refused."""
    file = (DATA_ROOT / path).resolve()
    if not file.is_relative_to(DATA_ROOT / "data"):
        raise ValueError(f"not a dataset path: {path}")
    if not file.is_file():
        raise FileNotFoundError(path)
    return file


def source_of(path: str) -> str:
    return "uploads" if path.startswith("uploads/") else path.split("/")[1]  # "data/<source>/..."


def build_scene(path: str) -> Scene:
    """Pick the builder for a drawing. Each builder is build_scene(path, file) -> Scene."""
    if path.startswith("uploads/"):  # DXF uploads are parsed, raster images detected; the API sends the rest to vision
        from .brain.vision import drawing_file
        if path.lower().endswith(".dxf"):
            from .parsers import swiss
            return swiss.build_scene(path, drawing_file(path))
        if path.lower().endswith((".png", ".jpg", ".jpeg")):
            from .detector import raster
            return raster.build_scene(path, drawing_file(path), upload=True)
        raise NotImplementedError("no parser for this upload type")
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
    if not path.startswith("uploads/"):  # uploads are checked by build_scene; their names are content hashes
        dataset_file(path)
    cached = CACHE / "scenes" / source_of(path) / (hashlib.md5(path.encode()).hexdigest() + ".json")
    from .brain.room_types import add_room_types  # room labels in any language -> room types (Küche -> kitchen)
    fresh = not (READ_CACHE and cached.exists())
    scene = build_scene(path) if fresh else Scene.model_validate_json(cached.read_text())
    if path.startswith("uploads/") and scene.tier == "detect" and "room" not in scene.covers:
        # A PNG or JPG upload: the detector finds doors, windows and fixtures, and the vision reader adds rooms by
        # type, apartments and floors, so it gives what a PDF upload gives. Done here, not in build_scene, so scenes
        # cached before this (or while the reader was down) get their rooms the next time they are opened.
        from .brain.vision import add_rooms
        fresh = add_rooms(scene, path) or fresh
    scene = add_room_types(scene)
    if fresh:
        _BUILT_NOW.add(path)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(scene.model_dump_json())
    return scene


def answer(path: str, question: str) -> Answer:
    scene = None
    steps = []
    try:
        scene = get_scene(path)
        if path in _BUILT_NOW:  # the first question on a drawing carries the scene's build cost
            _BUILT_NOW.discard(path)
            steps += scene.trace
        from .brain.plan import plan_with_trace
        plan, plan_steps = plan_with_trace(question, scene)
        steps += plan_steps
        result = execute(plan, scene)
        from .brain.vision import checked
        result = checked(result, plan, scene)  # rooms the vision reader added to an upload: no surer than the reader
    except Exception as e:  # a crashed builder (e.g. an undeployed Modal app) escalates instead of failing the answer
        steps += getattr(e, "steps", [])  # the planner's cost when it judged the question unsupported
        result = Answer(error=str(e) if isinstance(e, NotImplementedError) else f"{type(e).__name__}: {e}")
    result.trace = steps + result.trace
    if result.answer is None or result.confidence < ESCALATE_BELOW:
        try:
            from .brain.escalate import escalate
            result = escalate(question, path, scene, result)
        except NotImplementedError:
            pass
    return result
