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


IMAGES = (".png", ".jpg", ".jpeg")


def read_as_image(path: str) -> bool:
    """An image read like an upload: detector plus vision rooms. Every image except cubicasa5k, which has its own room
    reader; a plan image in ifc_models is a picture, not a model."""
    return path.lower().endswith(IMAGES) and (path.startswith("uploads/") or source_of(path) != "cubicasa5k")


def build_scene(path: str) -> Scene:
    """Pick the builder for a drawing. Each builder is build_scene(path, file) -> Scene."""
    if read_as_image(path):
        from .brain.vision import drawing_file
        from .detector import alike, raster
        scene = raster.build_scene(path, drawing_file(path), upload=True)
        alike.leave_out_rejected(scene, drawing_file(path))  # symbols users rejected on earlier drawings
        return scene
    if path.startswith("uploads/"):  # DXF, IFC and vector PDF uploads are parsed; the rest go to vision
        from .brain.vision import drawing_file
        if path.lower().endswith(".ifc"):
            from .parsers import ifc
            return ifc.build_scene(path, drawing_file(path))
        if path.lower().endswith(".dxf"):
            from .parsers import swiss
            return swiss.build_scene(path, drawing_file(path))
        if path.lower().endswith(".pdf"):  # raises NotImplementedError when the PDF is not a plan it can read
            from .parsers import pdf
            return pdf.build_scene(path, drawing_file(path))
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
    try:
        scene = None if fresh else Scene.model_validate_json(cached.read_bytes())
    except ValueError:  # written before scenes were saved as UTF-8 (Windows wrote cp1252): build it again
        scene, fresh = None, True
    if scene and scene.tier == "parse":  # a parsed scene from an older version of its builder: build it again
        from .parsers import ifc, pdf, swiss
        current = {b.split("@")[0]: b for b in (ifc.BUILDER, pdf.BUILDER, swiss.BUILDER, swiss.UPLOAD_BUILDER)}
        if current.get(scene.builder.split("@")[0], scene.builder) != scene.builder:
            scene, fresh = None, True
    scene = scene or build_scene(path)
    if read_as_image(path) and scene.tier == "detect" and "room" not in scene.covers:
        # A PNG or JPG: the detector finds doors, windows and fixtures, and the vision reader adds rooms by
        # type, apartments and floors, so it gives what a PDF upload gives. Done here, not in build_scene, so scenes
        # cached before this (or while the reader was down) get their rooms the next time they are opened.
        from .brain.vision import add_rooms
        fresh = add_rooms(scene, path) or fresh
    scene = add_room_types(scene)
    metadata_changed = False
    if path.lower().endswith(IMAGES):
        from .image_room_links import attach_room_links
        from .brain.vision import drawing_file
        metadata_changed = attach_room_links(scene, drawing_file(path))
        from .brain.room_link_vision import apply_cached
        metadata_changed = bool(apply_cached(scene)) or metadata_changed
    if fresh:
        _BUILT_NOW.add(path)
    if fresh or metadata_changed:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(scene.model_dump_json(), encoding="utf-8")  # IFC labels like "Ściana" are not in cp1252
    return scene


def answer(path: str, question: str, assumptions: dict | None = None) -> Answer:
    """assumptions: values the user supplies for what the drawing does not hold (a ceiling height, a price)."""
    scene = None
    steps = []
    settled = False  # the data-first resolver answered or abstained on a parsed file: nothing else may read sizes off it
    try:
        scene = get_scene(path)
        if path in _BUILT_NOW:  # the first question on a drawing carries the scene's build cost
            _BUILT_NOW.discard(path)
            steps += scene.trace
        from .brain.plan import cached_plan
        found = cached_plan(question, scene)
        if found:  # the free path: a question shape planned before, answered exactly as before
            plan, plan_steps = found
            steps += plan_steps
            from .brain.room_link_vision import execute_with_links
            result = execute_with_links(plan, scene)
            from .brain.vision import checked
            result = checked(result, plan, scene)  # rooms the vision reader added to an upload: no surer than the reader
        else:  # any other wording: look at what the scene holds, then decide (one model call, none on a repeat)
            from .brain.resolve import resolve
            result = resolve(question, scene, assumptions)
            settled = scene.tier == "parse"
    except Exception as e:  # a crashed builder (e.g. an undeployed Modal app) escalates instead of failing the answer
        steps += getattr(e, "steps", [])  # the planner's cost when it judged the question unsupported
        result = Answer(error=str(e) if isinstance(e, NotImplementedError) else f"{type(e).__name__}: {e}")
    result.trace = steps + result.trace
    from .brain.room_link_vision import refinement_exhausted
    if settled or refinement_exhausted(result):
        return result  # a parsed file's data settled it, or an exact scoped count already tried vision: no paid agent loops
    if result.answer is None or result.confidence < ESCALATE_BELOW:
        try:
            from .brain.escalate import escalate
            result = escalate(question, path, scene, result)
        except NotImplementedError:
            pass
    if result.answer is not None:
        from .reliability import measured
        seen = measured(result.tier, result.confidence)
        if seen:
            result.confidence_note = f"{result.confidence_note or ''} {seen}".strip()
    return result
