"""Lane C. Gemini reads a drawing once and returns a Scene: rooms, floors, doors, windows, fixtures, each with a box.

The model is a sensor: it lists what it sees with boxes, and the executor does the counting, so every answer has
evidence. Used for drawings that have no builder yet or never will (rasters before the detector, hand drawings).
Lanes A and B may call read_scene from their builders, for example for room types on rasters.
"""
import base64
import hashlib

import os
import time

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from .. import pipeline
from ..costs import llm_step
from ..schemas import Entity, Page, Scene, SUBTYPES
from .plan import GEMINI_URL

VISION_MODEL = os.environ.get("BLUEPRINTS_VISION_MODEL", "gemini-3.8-flash")
ROOM_TYPES = sorted(SUBTYPES["room"])
FIXTURE_TYPES = ["toilet", "sink", "bathtub", "shower"]


class _Item(BaseModel):
    box_2d: list[int] = Field(min_length=4, max_length=4)


class _Sides(BaseModel):  # one rectangle of a room; each side is the chain of numbers printed along it
    a: list[float]
    b: list[float]
    unit: Literal["mm", "cm", "m"]


class _Room(_Item):
    label: str | None = None
    number: str | None = None
    apartment: str | None = None
    printed_area_m2: float | None = None
    printed_sides: list[_Sides] | None = None
    type: Literal[tuple(ROOM_TYPES)]


TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}


class _Named(_Item):
    label: str | None = None


class _Fixture(_Item):
    type: Literal[tuple(FIXTURE_TYPES)]


class _Reply(BaseModel):
    """The structured output the model must return; JSON mode alone let Flash emit broken JSON on ~18% of drawings."""
    is_floor_plan: bool
    drawing_kind: str  # "floor plan", or what it is instead: "section", "elevation", "site plan", "photo", ...
    floors: list[_Item]
    apartments: list[_Named]
    rooms: list[_Room]
    doors: list[_Item]
    windows: list[_Item]
    fixtures: list[_Fixture]


PROMPT = f"""Look at this drawing. If it is a floor plan, list everything below that is drawn, as JSON only:
{{"is_floor_plan": true or false, "drawing_kind": "floor plan", or what it is instead (section, elevation, detail, site plan, photo, schedule, text),
 "floors": [{{"box_2d": [ymin, xmin, ymax, xmax]}}],
 "apartments": [{{"label": "the apartment's name as written, e.g. A", "box_2d": [...]}}],
 "rooms": [{{"label": "the room's name only", "number": "the room's number, e.g. 3.01, or null",
            "apartment": "the apartment it belongs to, e.g. A, or null", "printed_area_m2": number or null,
            "printed_sides": [{{"a": [numbers], "b": [numbers], "unit": "mm", "cm" or "m"}}] or null,
            "type": one of {ROOM_TYPES}, "box_2d": [...]}}],
 "doors": [{{"box_2d": [...]}}],
 "windows": [{{"box_2d": [...]}}],
 "fixtures": [{{"type": one of {FIXTURE_TYPES}, "box_2d": [...]}}]}}
box_2d is [ymin, xmin, ymax, xmax] scaled 0-1000 to the image.
- A room tag often stacks several things, e.g. "Whg. A / Wohnen/Essen / 3.05 / 18.36 m²": split it into
  apartment "A", label "Wohnen/Essen", number "3.05", printed_area_m2 18.36. Copy the label exactly as written.
- printed_sides: for a room with no printed area, its sides copied from the dimension lines along it. A rectangular
  room is one entry: a is the numbers printed along its width, b along its length, e.g. {{"a": [2480], "b": [3680]}}.
  When a side is covered by a chain of several dimensions, list every number of the chain, e.g. "b": [2000, 1040].
  An L-shaped or stepped room is split into rectangles that do not overlap, one entry each. Copy the numbers exactly,
  do not add, multiply or convert them; use null only when the plan prints no dimensions along the room.
- apartments: one entry per apartment or dwelling unit shown, with its outline (empty for a single home without names).
- floors: one entry per separate floor plan drawn on the sheet (a sheet may show 2-4 floors side by side).
- doors: doorways fitted with a door leaf (a swing arc or a leaf); an opening without a leaf is not a door.
- windows: each window unit in a wall.
- fixtures: every toilet bowl, sink or washbasin (kitchen and bathroom; a double sink is one), bathtub and shower.
- rooms: every room. Read labels in any language and abbreviation (for example MH bedroom, KH/PH/KPH bathroom,
  WC toilet room, K/KK/KEITTIÖ kitchen, S/SAUNA sauna, PARV balcony). bathroom excludes separate toilet rooms,
  which are wc_room; kitchen includes kitchenettes; balcony includes glazed balconies.
List each item once. Leave out anything you cannot see.
If the drawing is not a floor plan, set is_floor_plan false, name what it is in drawing_kind, and leave every list empty."""


UPLOADS = pipeline.CACHE / "uploads"


def drawing_file(path: str):
    """A dataset path ("data/...") or an uploaded file ("uploads/<id>.<ext>"); anything else is refused."""
    if path.startswith("uploads/"):
        file = (pipeline.CACHE / path).resolve()
        if not file.is_relative_to(UPLOADS.resolve()) or not file.is_file():
            raise FileNotFoundError(path)
        return file
    return pipeline.dataset_file(path)


def _page(path: str):
    """The page image for the viewer and the model: the raster itself, or the first PDF page rendered once."""
    from .baseline import page_png
    file = drawing_file(path)
    png = page_png(file)
    if file.suffix.lower() == ".pdf":
        rel = f"pages/{hashlib.md5(path.encode()).hexdigest()}.png"
        (pipeline.CACHE / "pages").mkdir(parents=True, exist_ok=True)
        (pipeline.CACHE / rel).write_bytes(png)
        image = rel
    else:
        image = path
    import pymupdf
    pix = pymupdf.Pixmap(png)
    return png, Page(image=image, width=pix.width, height=pix.height)


def _geom(box) -> list[tuple[float, float]]:
    ymin, xmin, ymax, xmax = (min(1.0, max(0.0, float(v) / 1000)) for v in box)
    return [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]


def printed_area(rects) -> tuple[float | None, str]:
    """m² from the rectangles the model copied off the dimension lines: the model copies numbers, the code adds up each
    chain and multiplies. Any side outside 0.3-50 m means a misread unit or number, so the whole room gets no area."""
    try:
        parts = [(sum(map(float, r["a"])) * TO_M[r["unit"]], sum(map(float, r["b"])) * TO_M[r["unit"]]) for r in rects]
    except (TypeError, KeyError, ValueError):
        return None, ""
    if not parts or not all(0.3 <= s <= 50 for part in parts for s in part):
        return None, ""
    return round(sum(a * b for a, b in parts), 2), " + ".join(f"{a:g} m x {b:g} m" for a, b in parts)


def to_scene(path: str, page: Page, found: dict) -> Scene:
    """Turn the model's list into a Scene. Items with a bad box or an unknown type are skipped, not guessed.
    A drawing the model says is not a floor plan gets an empty scene that covers nothing, marked in the builder name,
    so every question on it is refused rather than answered with a confident zero."""
    if found.get("is_floor_plan") is False:
        kind = str(found.get("drawing_kind") or "not a floor plan")[:40].replace(":", " ")
        return Scene(path=path, builder=f"{BUILDER}{NOT_A_PLAN}{kind}", tier="detect", covers=[], pages=[page])
    entities: list[Entity] = []

    def add(kind: str, subtype: str | None, item: dict, label: str | None = None, **more):
        try:
            geom = _geom(item["box_2d"])
        except (KeyError, TypeError, ValueError):
            return
        entities.append(Entity(id=f"vis-{kind}-{len(entities)}", type=kind, subtype=subtype, label=label,
                               geom=geom, ref=f"{VISION_MODEL}:{len(entities)}", **more))

    for item in found.get("floors", []):
        add("level", None, item, label=f"floor {sum(e.type == 'level' for e in entities) + 1}")
    for item in found.get("apartments", []):
        add("apartment", None, item, label=item.get("label"))
    for item in found.get("rooms", []):
        subtype = item.get("type") if item.get("type") in ROOM_TYPES else "other"
        area, sides, attrs = item.get("printed_area_m2"), item.get("printed_sides"), {}
        if not (isinstance(area, (int, float)) and area > 0):
            area = None
            area, how = printed_area(sides)
            if area:
                attrs = {"area_from": f"printed dimensions {how} (dimension lines, may include walls)"}
        add("room", subtype, item, label=item.get("label"), number=item.get("number"), apartment=item.get("apartment"),
            outdoor=subtype == "balcony", qty={"m2": float(area)} if area else {}, attrs=attrs)
    for kind in ("door", "window"):
        for item in found.get(kind + "s", []):
            add(kind, None, item)
    for item in found.get("fixtures", []):
        if item.get("type") in FIXTURE_TYPES:
            add("fixture", item["type"], item)
    covers = ["level", "apartment", "room", "door", "window"] + [f"room:{t}" for t in ROOM_TYPES] + [f"fixture:{t}" for t in FIXTURE_TYPES]
    return Scene(path=path, builder=BUILDER, tier="detect", covers=covers, pages=[page], entities=entities)


BUILDER = f"vision:{VISION_MODEL}@5"  # bump the version when the prompt or the conversion changes; it keys the cache
NOT_A_PLAN = ":not-a-floor-plan:"  # builder suffix for drawings the reader refused, followed by what they are


def not_a_floor_plan(scene: Scene) -> str | None:
    """What the drawing is instead, if the vision reader said it is not a floor plan."""
    return scene.builder.split(NOT_A_PLAN, 1)[1] if NOT_A_PLAN in scene.builder else None


def read_scene(path: str) -> tuple[Scene, bool]:
    """The vision scene for a drawing and whether it was built just now (so its cost belongs to this answer)."""
    cached = pipeline.CACHE / "scenes" / "vision" / (hashlib.md5(f"{BUILDER}:{path}".encode()).hexdigest() + ".json")
    if pipeline.READ_CACHE and cached.exists():
        scene = Scene.model_validate_json(cached.read_text())
        if _image_connections(scene, path):
            cached.write_text(scene.model_dump_json(), encoding="utf-8")
        return scene, False  # CPU enrichment must not bill the original vision read again
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise NotImplementedError("lane C: no scene for this drawing, and GEMINI_API_KEY is not set for the vision reader")
    import openai
    png, page = _page(path)
    client = openai.OpenAI(api_key=key, base_url=GEMINI_URL, max_retries=4, timeout=180)
    schema = {"type": "json_schema", "json_schema": {"name": "FloorPlan", "schema": _Reply.model_json_schema()}}
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        {"type": "text", "text": PROMPT}]}]
    steps, found = [], None
    for _ in range(2):  # one retry: a malformed reply is rare with the schema, but not impossible
        t0 = time.perf_counter()
        try:
            reply = client.chat.completions.create(model=VISION_MODEL, response_format=schema, messages=messages)
        except openai.OpenAIError as e:
            raise NotImplementedError(f"lane C: vision call failed: {type(e).__name__}: {e}") from None
        steps.append(llm_step("scene", VISION_MODEL, reply.usage.prompt_tokens, reply.usage.completion_tokens, (time.perf_counter() - t0) * 1000))
        try:
            found = _Reply.model_validate_json(reply.choices[0].message.content or "").model_dump()
            break
        except ValueError:
            continue
    if found is None:
        raise NotImplementedError("lane C: the vision reply failed validation twice")
    scene = to_scene(path, page, found)
    listed = Counter(e.type for e in scene.entities)
    steps[-1].detail = ("Listed " + ", ".join(f"{n} {kind}" for kind, n in listed.items()) if listed else
                        f"Read as {not_a_floor_plan(scene) or 'a floor plan with nothing listed'}")
    scene.trace = steps
    _image_connections(scene, path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(scene.model_dump_json())
    return scene, True


def _image_connections(scene: Scene, path: str) -> bool:
    if not path.lower().endswith(pipeline.IMAGES):
        return False
    from ..image_room_links import attach_room_links
    from .room_link_vision import apply_cached
    changed = attach_room_links(scene, drawing_file(path))
    return bool(apply_cached(scene)) or changed


ADDED_KINDS = ("room", "apartment", "level")  # what add_rooms takes from the vision reader


def from_reader(plan, scene: Scene) -> bool:
    """Whether the answer rests on what add_rooms took from the vision reader, so it is rated as the reader is."""
    return f"+{BUILDER}" in scene.builder and any(q.type in ADDED_KINDS for q in plan.queries.values())


def checked(result, plan, scene: Scene):
    """An Answer about the kinds add_rooms took from the vision reader is no surer than the reader. When the reader
    listed nothing the question asks for, that is weak evidence of none (it named 5 zones on a sheet of 48 dorm
    rooms): the answer is withdrawn, so the pipeline hands the question to the agent with the reason."""
    from ..executor import select
    from .escalate import VISION_CONFIDENCE
    asked = [q for q in plan.queries.values() if q.type in ADDED_KINDS]
    if result.answer is None or f"+{BUILDER}" not in scene.builder or not asked:
        return result
    none = [f"{q.type}:{q.subtype}" if q.subtype else q.type for q in asked if not select(q, scene)]
    if none:
        return result.model_copy(update={"answer": None, "unit": None, "confidence": 0.0, "evidence": [], "error":
            f"the vision reader listed no {', '.join(none)} on this drawing, which is weak evidence that there are "
            f"none: look at the drawing itself before answering zero"})
    return result.model_copy(update={"confidence": min(result.confidence, VISION_CONFIDENCE), "confidence_note":
        f"{result.confidence_note or ''} Capped at {VISION_CONFIDENCE:.0%}: the rooms come from the vision model's "
        f"reading, which gets a fixed score.".strip()})


def add_rooms(scene: Scene, path: str, seen: Scene | None = None) -> bool:
    """Give a detector scene of an upload what a PDF upload gets: rooms by type, apartments and floors, from the
    vision reader's scene of the same drawing (seen; read here when not passed). The detector keeps doors, windows
    and fixtures, where its boxes are the better ones. Returns whether the scene changed: it does not when the reader
    is unavailable or saw no floor plan, and the scene then covers only what the detector finds, as before."""
    from .escalate import VISION_CONFIDENCE
    if seen is None:
        try:
            seen = read_scene(path)[0]
        except NotImplementedError:
            return False
    kinds = ADDED_KINDS
    covers = [c for c in seen.covers if c.partition(":")[0] in kinds and c not in scene.covers]
    if not covers:
        return False
    # The model's list is not certain: the same confidence an answer on a vision-only scene gets.
    scene.entities += [e.model_copy(update={"conf": VISION_CONFIDENCE}) for e in seen.entities if e.type in kinds]
    scene.covers += covers
    scene.trace += seen.trace
    scene.builder += f"+{BUILDER}"
    return True


if __name__ == "__main__":  # self-check of the conversion, no model call
    page = Page(image="data/x.png", width=100, height=50)
    s = to_scene("data/x.png", page, {
        "floors": [{"box_2d": [0, 0, 1000, 500]}, {"box_2d": [0, 500, 1000, 1000]}],
        "apartments": [{"label": "A", "box_2d": [0, 0, 500, 500]}],
        "rooms": [{"label": "MH", "type": "bedroom", "box_2d": [100, 100, 400, 400], "number": "3.01", "apartment": "A", "printed_area_m2": 12.5},
                  {"label": "?", "type": "attic", "box_2d": [1, 1, 2, 2]}, {"label": "Balkon", "type": "balcony", "box_2d": [1, 1, 2, 2]}],
        "doors": [{"box_2d": [10, 10, 20, 20]}, {"box_2d": "bad"}],
        "windows": [{"box_2d": [0, 0, 1200, 10]}],
        "fixtures": [{"type": "sink", "box_2d": [5, 5, 6, 6]}, {"type": "bidet", "box_2d": [5, 5, 6, 6]}]})
    kinds = [(e.type, e.subtype) for e in s.entities]
    assert kinds.count(("level", None)) == 2 and ("room", "bedroom") in kinds and ("room", "other") in kinds
    assert kinds.count(("door", None)) == 1 and ("fixture", "sink") in kinds and len(kinds) == 9
    bedroom = next(e for e in s.entities if e.subtype == "bedroom")
    assert (bedroom.number, bedroom.apartment, bedroom.qty) == ("3.01", "A", {"m2": 12.5})
    assert next(e for e in s.entities if e.subtype == "balcony").outdoor
    sized = to_scene("data/x.png", page, {"rooms": [
        {"label": "Kitchen", "type": "kitchen", "box_2d": [0, 0, 9, 9], "printed_sides": [{"a": [2480], "b": [3680], "unit": "mm"}]},
        {"label": "K", "type": "kitchen", "box_2d": [0, 0, 9, 9], "printed_sides": [{"a": [2480], "b": [3680], "unit": "m"}]},
        {"label": "B", "type": "bedroom", "box_2d": [0, 0, 9, 9], "printed_area_m2": 12.0, "printed_sides": [{"a": [1], "b": [1], "unit": "m"}]},
        # an L-shaped room as two rectangles, one side a chain of three dimensions: 3.04 x 7.44 + 1 x 2
        {"label": "Living", "type": "living_room", "box_2d": [0, 0, 9, 9], "printed_sides": [
            {"a": [2720, 320], "b": [2000, 1040, 4400], "unit": "mm"}, {"a": [1000], "b": [2000], "unit": "mm"}]},
        {"label": "Odd", "type": "other", "box_2d": [0, 0, 9, 9], "printed_sides": [{"a": ["x"], "b": [1], "unit": "m"}]}]})
    assert [e.qty.get("m2") for e in sized.entities] == [9.13, None, 12.0, 24.62, None] and "area_from" in sized.entities[0].attrs
    assert "3.04 m x 7.44 m + 1 m x 2 m" in sized.entities[3].attrs["area_from"]
    section = to_scene("data/x.png", page, {"is_floor_plan": False, "drawing_kind": "section", "doors": [{"box_2d": [1, 1, 2, 2]}]})
    assert not section.entities and not section.covers and not_a_floor_plan(section) == "section"
    assert not_a_floor_plan(s) is None
    assert all(0 <= v <= 1 for e in s.entities for p in e.geom for v in p)
    # a detector scene of an upload takes the rooms, apartment and floors, and keeps its own doors and fixtures
    door = Entity(id="det-0", type="door", conf=0.9)
    det = Scene(path="uploads/x.png", builder="fixtures-6:tiled@2", tier="detect", covers=["door", "fixture:sink"], pages=[page], entities=[door])
    assert add_rooms(det, "uploads/x.png", s) and not add_rooms(det, "uploads/x.png", s)  # the second time adds nothing
    assert Counter(e.type for e in det.entities) == {"door": 1, "room": 3, "apartment": 1, "level": 2}
    assert {"room", "room:bedroom", "level", "apartment", "door"} <= set(det.covers) and "window" not in det.covers
    assert det.entities[0].conf == 0.9 and all(e.conf < 1 for e in det.entities[1:]) and "+vision:" in det.builder
    assert not_a_floor_plan(det) is None and not add_rooms(det.model_copy(update={"covers": ["door"]}), "uploads/x.png", section)
    Scene.model_validate(det.model_dump())
    # no sauna was listed: weak evidence of none, so no answer (the pipeline escalates); a listed bedroom is as sure
    # as the reader; the detector's own doors are left alone
    from ..executor import execute
    from ..schemas import Query, QueryPlan
    count = lambda kind, sub=None: QueryPlan(queries={"n": Query(op="count", type=kind, subtype=sub)}, unit="count")
    ask = lambda scene, *kind: checked(execute(count(*kind), scene), count(*kind), scene)
    none = ask(det, "room", "sauna")
    assert none.answer is None and "room:sauna" in none.error
    assert (ask(det, "room", "bedroom").answer, ask(det, "door").answer, ask(det, "door").confidence) == (1, 1, 0.9)
    assert 0.5 < ask(det, "room", "bedroom").confidence < 1
    assert ask(s, "room", "sauna").answer == 0  # a vision-only scene is rated by answer_from_vision
    print("ok: vision scene conversion")
