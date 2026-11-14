"""Lane C. Gemini reads a drawing once and returns a Scene: rooms, floors, doors, windows, fixtures, each with a box.

The model is a sensor: it lists what it sees with boxes, and the executor does the counting, so every answer has
evidence. Used for drawings that have no builder yet or never will (rasters before the detector, hand drawings).
Lanes A and B may call read_scene from their builders, for example for room types on rasters.
"""
import base64
import hashlib

import os
import time

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


class _Room(_Item):
    label: str | None = None
    number: str | None = None
    apartment: str | None = None
    printed_area_m2: float | None = None
    type: Literal[tuple(ROOM_TYPES)]


class _Named(_Item):
    label: str | None = None


class _Fixture(_Item):
    type: Literal[tuple(FIXTURE_TYPES)]


class _Reply(BaseModel):
    """The structured output the model must return; JSON mode alone let Flash emit broken JSON on ~18% of drawings."""
    floors: list[_Item]
    apartments: list[_Named]
    rooms: list[_Room]
    doors: list[_Item]
    windows: list[_Item]
    fixtures: list[_Fixture]


PROMPT = f"""This image is a floor plan. List everything below that is drawn, as JSON only:
{{"floors": [{{"box_2d": [ymin, xmin, ymax, xmax]}}],
 "apartments": [{{"label": "the apartment's name as written, e.g. A", "box_2d": [...]}}],
 "rooms": [{{"label": "the room's name only", "number": "the room's number, e.g. 3.01, or null",
            "apartment": "the apartment it belongs to, e.g. A, or null", "printed_area_m2": number or null,
            "type": one of {ROOM_TYPES}, "box_2d": [...]}}],
 "doors": [{{"box_2d": [...]}}],
 "windows": [{{"box_2d": [...]}}],
 "fixtures": [{{"type": one of {FIXTURE_TYPES}, "box_2d": [...]}}]}}
box_2d is [ymin, xmin, ymax, xmax] scaled 0-1000 to the image.
- A room tag often stacks several things, e.g. "Whg. A / Wohnen/Essen / 3.05 / 18.36 m²": split it into
  apartment "A", label "Wohnen/Essen", number "3.05", printed_area_m2 18.36. Copy the label exactly as written.
- apartments: one entry per apartment or dwelling unit shown, with its outline (empty for a single home without names).
- floors: one entry per separate floor plan drawn on the sheet (a sheet may show 2-4 floors side by side).
- doors: doorways fitted with a door leaf (a swing arc or a leaf); an opening without a leaf is not a door.
- windows: each window unit in a wall.
- fixtures: every toilet bowl, sink or washbasin (kitchen and bathroom; a double sink is one), bathtub and shower.
- rooms: every room. Read labels in any language and abbreviation (for example MH bedroom, KH/PH/KPH bathroom,
  WC toilet room, K/KK/KEITTIÖ kitchen, S/SAUNA sauna, PARV balcony). bathroom excludes separate toilet rooms,
  which are wc_room; kitchen includes kitchenettes; balcony includes glazed balconies.
List each item once. Leave out anything you cannot see."""


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


def to_scene(path: str, page: Page, found: dict) -> Scene:
    """Turn the model's list into a Scene. Items with a bad box or an unknown type are skipped, not guessed."""
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
        area = item.get("printed_area_m2")
        add("room", subtype, item, label=item.get("label"), number=item.get("number"), apartment=item.get("apartment"),
            outdoor=subtype == "balcony", qty={"m2": float(area)} if isinstance(area, (int, float)) and area > 0 else {})
    for kind in ("door", "window"):
        for item in found.get(kind + "s", []):
            add(kind, None, item)
    for item in found.get("fixtures", []):
        if item.get("type") in FIXTURE_TYPES:
            add("fixture", item["type"], item)
    covers = ["level", "apartment", "room", "door", "window"] + [f"room:{t}" for t in ROOM_TYPES] + [f"fixture:{t}" for t in FIXTURE_TYPES]
    return Scene(path=path, builder=BUILDER, tier="detect", covers=covers, pages=[page], entities=entities)


BUILDER = f"vision:{VISION_MODEL}@2"  # bump the version when the prompt or the conversion changes; it keys the cache


def read_scene(path: str) -> tuple[Scene, bool]:
    """The vision scene for a drawing and whether it was built just now (so its cost belongs to this answer)."""
    cached = pipeline.CACHE / "scenes" / "vision" / (hashlib.md5(f"{BUILDER}:{path}".encode()).hexdigest() + ".json")
    if pipeline.READ_CACHE and cached.exists():
        return Scene.model_validate_json(cached.read_text()), False
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
    scene.trace = steps
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(scene.model_dump_json())
    return scene, True


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
    assert all(0 <= v <= 1 for e in s.entities for p in e.geom for v in p)
    print("ok: vision scene conversion")
