"""Shared glue: the one path every question takes. Lanes fill in the functions this file calls.

Lane modules are imported inside the functions on purpose: a missing package in one lane (ifcopenshell, modal,
openai) must not break the others. Keep heavy imports inside functions in your own modules too.
"""
import hashlib
import os
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from .executor import execute
from .schemas import Answer, Scene

DATA_ROOT = Path(os.environ.get("BLUEPRINTS_DATA", "junction-dataset")).resolve()
CACHE = Path(os.environ.get("BLUEPRINTS_CACHE", ".cache")).resolve()
READ_CACHE = True  # eval.py --fresh turns this off while you iterate on a builder
ESCALATE_BELOW = 0.5  # ponytail: one fixed threshold; tune it on the eval's cost-against-accuracy curve
_BUILT_NOW: set[str] = set()  # scenes built in this process; the next answer on that drawing pays their cost
# Set per request by api.py. GUEST: a visitor without the password, answered from caches and the data, no model call.
# FRESH: a signed-in user turned cached answers off, so everything is asked of the models again: the scene (detector,
# segmenter, vision reader), plans, decisions, name matches and reviews.
GUEST = ContextVar("guest", default=False)
FRESH = ContextVar("fresh", default=False)


def use_cache() -> bool:
    """Whether a model's cached result may stand in for calling it."""
    return READ_CACHE and not FRESH.get()


def model_key(name: str = "GEMINI_API_KEY") -> str | None:
    """The model's API key, or None for a guest: every model call already falls back to caches without a key."""
    return None if GUEST.get() else os.environ.get(name)


def scene_file(path: str) -> Path:
    return CACHE / "scenes" / source_of(path) / (hashlib.md5(path.encode()).hexdigest() + ".json")


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
        if path.lower().endswith((".dxf", ".pdf")):
            # The Swiss renderer's layers and styles first (exact), then the generic reader (rooms from a room layer
            # or fill, else from the walls). Each raises NotImplementedError on a file it cannot read; when both do,
            # the upload is left to the vision reader.
            from .parsers import dxf, pdf, swiss, vector_pdf
            known, generic = (swiss, dxf) if path.lower().endswith(".dxf") else (pdf, vector_pdf)
            try:
                return known.build_scene(path, drawing_file(path))
            except NotImplementedError:
                return generic.build_scene(path, drawing_file(path))
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


def get_scene(path: str) -> Scene:
    """The scene for a drawing, from .cache/scenes/<source>/ when it was built before; built by the models again when
    cached answers are off."""
    return _scene(path, FRESH.get())


# ponytail: with cached answers off a drawing is rebuilt once per server process, then held like any other; key it on
# the request if every question must rebuild (30 s on a large sheet).
@lru_cache(maxsize=16)
def _scene(path: str, rebuild: bool) -> Scene:
    if not path.startswith("uploads/"):  # uploads are checked by build_scene; their names are content hashes
        dataset_file(path)
    cached = scene_file(path)
    from .brain.room_types import add_room_types  # room labels in any language -> room types (Küche -> kitchen)
    fresh = not (READ_CACHE and not rebuild and cached.exists())
    try:
        scene = None if fresh else Scene.model_validate_json(cached.read_bytes())
    except ValueError:  # written before scenes were saved as UTF-8 (Windows wrote cp1252): build it again
        scene, fresh = None, True
    if scene and scene.tier == "parse":  # a parsed scene from an older version of its builder: build it again
        from .parsers import ifc, pdf, swiss
        current = {b.split("@")[0]: b for b in (ifc.BUILDER, pdf.BUILDER, swiss.BUILDER, swiss.UPLOAD_BUILDER)}
        if current.get(scene.builder.split("@")[0], scene.builder) != scene.builder:
            scene, fresh = None, True
    if scene and read_as_image(path):  # detected by an older tiled detector: detected again (the vision reader runs again too)
        from .detector import raster
        if raster.stale(scene):
            scene, fresh = None, True
    if scene is None:
        scene = build_scene(path)
        if path.startswith("uploads/"):  # the upload's floor-plan and orientation check belongs to the drawing's cost
            from .brain.vision import glance_steps
            scene.trace = glance_steps(path) + scene.trace
    if read_as_image(path) and scene.tier == "detect" and "room" not in scene.covers:
        # A PNG or JPG: the detector finds doors, windows and fixtures, and the vision reader adds rooms by
        # type, apartments and floors, so it gives what a PDF upload gives. Done here, not in build_scene, so scenes
        # cached before this (or while the reader was down) get their rooms the next time they are opened.
        from .brain.vision import add_rooms
        fresh = add_rooms(scene, path) or fresh
    if read_as_image(path) and "room" in scene.covers:
        # The reader's rooms are boxes. The segmenter turns them into outlines, and one printed room area gives the
        # scale for every other room's area, perimeter and wall area. Skipped when it is down; tried again next time.
        from .brain.vision import drawing_file
        from .detector import rooms
        try:
            fresh = rooms.outline_rooms(scene, drawing_file(path)) or fresh
        except NotImplementedError:
            pass
    if source_of(path) == "cubicasa5k":
        # Areas from the vision reader's printed room areas or apartment total, when its read of this plan is cached
        # (tools/guest_cases.py --warm reads the guest plans); a guest or a plain open never pays for a read.
        from .brain.vision import read_scene
        from .detector import rooms
        token = GUEST.set(True)  # no key: the cached read or nothing
        try:
            fresh = rooms.scale_rooms(scene, read_scene(path)[0]) or fresh
        except NotImplementedError:
            pass
        finally:
            GUEST.reset(token)
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


get_scene.cache_clear = _scene.cache_clear


def answer(path: str, question: str, assumptions: dict | None = None, history: list | None = None) -> Answer:
    """assumptions: values the user supplies for what the drawing does not hold (a ceiling height, a price).
    history: the conversation's earlier turns, from the client, so a follow-up question can refer to them. A question
    whose shape has a cached plan is complete by construction and is answered the same with or without it."""
    scene = None
    steps = []
    settled = False  # the data-first resolver answered or abstained on a parsed file: nothing else may read sizes off it
    try:
        scene = get_scene(path)
        if path in _BUILT_NOW:  # the first question on a drawing carries the scene's build cost
            _BUILT_NOW.discard(path)
            steps += scene.trace
        from .room_ranking import answer_question
        ranking = answer_question(question, scene)
        if ranking is not None:
            ranking.trace = steps + ranking.trace
            return ranking
        from .brain.plan import cached_plan
        found = cached_plan(question, scene)
        if not found and "\n\nSelected drawing object: " in question:
            # Explicit object questions use the endpoint-aware executor and its evidence cutoff.
            # The general SQL resolver remains the default for unscoped, free-form questions.
            from .brain.plan import plan_with_trace
            try:
                found = plan_with_trace(question, scene)
            except NotImplementedError as error:
                steps += getattr(error, "steps", [])
        if found:  # the free path: a question shape planned before, answered exactly as before
            plan, plan_steps = found
            steps += plan_steps
            from .brain.room_link_vision import execute_with_links
            result = execute_with_links(plan, scene)
            from .brain.vision import checked, from_reader
            from .calibration import calibrate
            result = checked(result, plan, scene)  # rooms the vision reader added to an upload: no surer than the reader
            result = calibrate(result, "vision" if from_reader(plan, scene) else scene.tier)  # raw score -> measured rate
        else:  # any other wording: look at what the scene holds, then decide (one model call, none on a repeat)
            from .brain.resolve import resolve
            result = resolve(question, scene, assumptions, history)
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
            result = escalate(question, path, scene, result, **({"history": history} if history else {}))
        except NotImplementedError:
            pass
    if result.answer is not None:
        from .reliability import measured
        seen = measured(result.tier, result.confidence)
        if seen:
            result.confidence_note = f"{result.confidence_note or ''} {seen}".strip()
    return result
