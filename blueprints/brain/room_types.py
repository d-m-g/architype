"""Lane C. Room labels in any language -> the schema's room types, so "kitchen" finds rooms labelled Küche or Cucina.

pipeline.get_scene calls add_room_types on every scene. Rooms that have a label but no subtype get one. The scene
then covers room:<type> only when every room got a type, so a label we could not classify never turns into a false 0
(the executor refuses and the question escalates instead).

Labels are classified once each and cached: room_types.json (committed, the dataset's labels, reviewable by eye)
and .cache/room_types.json (labels first seen at run time). Unknown labels cost one Gemini call per drawing.

Check / seed:  uv run --env-file .env --extra brain --extra parsers python -m blueprints.brain.room_types [--seed]
"""
import json
import os
import sys
import time
from pathlib import Path

from .. import pipeline
from ..costs import llm_step
from ..schemas import SUBTYPES, Scene, TraceStep
from .plan import GEMINI_MODEL, GEMINI_URL

ROOM_TYPES = sorted(SUBTYPES["room"])
SEED_FILE = Path(__file__).parent / "room_types.json"
_known: dict[str, str] = {}

PROMPT = f"""Classify each room label from a floor plan into one of {ROOM_TYPES}. Labels are written as on the
drawing, in any language or abbreviation (German, French, Italian, Finnish, English...).
- bedroom: a bedroom or a plain habitable room (Zimmer, Schlafzimmer, Chambre, Camera, Stanza, Pièce, MH).
- living: living or dining room (Wohnen, Wohnen/Essen, Séjour, Soggiorno, OH).
- bathroom: a room with a bath or shower (Bad, Dusche, Bain, Bagno, KH, PH). wc_room: a separate toilet room (WC).
- kitchen: kitchen or kitchenette. balcony: balcony, loggia, terrace (outdoor). sauna: sauna.
- corridor: corridor, hall, entrance (Korridor, Gang, Entrée, Couloir, ET). storage: storage, closet, cellar (Reduit, Abstellraum, VH).
- other: anything else (stairwell, lift, shaft, technical room, laundry).
Reply with JSON only: an object mapping every label exactly as given to its type."""


def _key(label: str) -> str:
    return " ".join(label.split()).casefold()


def _load() -> dict[str, str]:
    if not _known:
        for file in (SEED_FILE, pipeline.CACHE / "room_types.json"):
            if file.exists():
                _known.update(json.loads(file.read_text(encoding="utf-8")))
    return _known


def classify(labels: list[str]) -> tuple[dict[str, str], list[TraceStep]]:
    """Type for each label; labels not cached go to Gemini in one call. Raises NotImplementedError without a key."""
    known = _load()
    todo = sorted({_key(l) for l in labels if l and _key(l) not in known})
    steps: list[TraceStep] = []
    if todo:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise NotImplementedError("lane C: unknown room labels and no GEMINI_API_KEY to classify them")
        import openai
        client = openai.OpenAI(api_key=key, base_url=GEMINI_URL, max_retries=3, timeout=60)
        t0 = time.perf_counter()
        try:
            reply = client.chat.completions.create(model=GEMINI_MODEL, response_format={"type": "json_object"}, temperature=0,
                                                   messages=[{"role": "system", "content": PROMPT},
                                                             {"role": "user", "content": json.dumps(todo, ensure_ascii=False)}])
            said = json.loads(reply.choices[0].message.content or "{}")
        except (openai.OpenAIError, json.JSONDecodeError) as e:
            raise NotImplementedError(f"lane C: room-type call failed: {type(e).__name__}") from None
        steps.append(llm_step("room_types", GEMINI_MODEL, reply.usage.prompt_tokens, reply.usage.completion_tokens,
                              (time.perf_counter() - t0) * 1000))
        new = {_key(k): v for k, v in (said.items() if isinstance(said, dict) else []) if v in ROOM_TYPES and _key(k) in todo}
        known.update(new)
        local = pipeline.CACHE / "room_types.json"
        cached = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps(cached | new, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return {l: known[_key(l)] for l in labels if l and _key(l) in known}, steps


def _inside(point, polygon) -> bool:
    """Ray casting: is point inside polygon (both in page coordinates)?"""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _type_from_fixtures(room, fixtures) -> str | None:
    """An unlabelled room is a bathroom if it holds a bathtub or shower, a WC room if it holds only a toilet."""
    if len(room.geom) < 3:
        return None
    held = {f.subtype for f in fixtures
            if f.geom and _inside((sum(p[0] for p in f.geom) / len(f.geom), sum(p[1] for p in f.geom) / len(f.geom)), room.geom)}
    if held & {"bathtub", "shower"}:
        return "bathroom"
    return "wc_room" if "toilet" in held else None


def add_room_types(scene: Scene) -> Scene:
    """Give rooms without a subtype one, in place: labelled rooms by their label, unlabelled ones by the fixtures
    inside them. Adds room:<type> to covers only if every room got a type, so an unknown room never reads as a 0."""
    rooms = [e for e in scene.entities if e.type == "room"]
    untyped = [e for e in rooms if e.subtype is None]
    if not untyped or "room" not in scene.covers:
        return scene
    try:
        types, steps = classify([e.label for e in untyped if e.label])
    except NotImplementedError:
        types, steps = {}, []  # unknown labels stay untyped; questions by room type then escalate
    fixtures = [e for e in scene.entities if e.type == "fixture"]
    # Fixtures were looked for, so an unlabelled room holding none is not a bathroom or WC; such rooms are shafts and
    # closets in practice. ponytail: assumes kitchens are always labelled; revisit if a plan draws an unnamed kitchen.
    fixtures_known = {"fixture:toilet", "fixture:bathtub", "fixture:shower"} <= set(scene.covers)
    for e in untyped:
        e.subtype = types.get(e.label) if e.label else (_type_from_fixtures(e, fixtures) or ("other" if fixtures_known and len(e.geom) >= 3 else None))
    scene.trace += steps
    if all(e.subtype for e in rooms):
        scene.covers = sorted(set(scene.covers) | {f"room:{t}" for t in ROOM_TYPES})
    return scene


def _seed() -> None:
    """Classify every room label the Swiss parser finds in the dataset and write room_types.json."""
    from concurrent.futures import ThreadPoolExecutor
    from ..parsers import swiss
    files = sorted((pipeline.DATA_ROOT / "data" / "swiss_dwellings").glob("*.dxf"))
    print(f"parsing {len(files)} Swiss plans for room labels", flush=True)

    def labels_of(dxf: Path) -> set[str]:
        path = f"data/swiss_dwellings/{dxf.stem}.pdf"
        try:
            return {e.label for e in swiss.build_scene(path, dxf.with_suffix(".pdf")).entities if e.type == "room" and e.label}
        except Exception:
            return set()

    with ThreadPoolExecutor(8) as pool:
        labels = sorted(set().union(*pool.map(labels_of, files)), key=_key)
    print(f"{len(labels)} distinct labels", flush=True)
    _known.clear()
    seeded: dict[str, str] = json.loads(SEED_FILE.read_text(encoding="utf-8")) if SEED_FILE.exists() else {}
    _known.update(seeded)
    for i in range(0, len(labels), 150):
        classify(labels[i:i + 150])
    out = {_key(l): _known[_key(l)] for l in labels if _key(l) in _known}
    SEED_FILE.write_text("{\n" + ",\n".join(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v)}" for k, v in sorted(out.items())) + "\n}\n",
                         encoding="utf-8")
    print(f"wrote {len(out)} labels to {SEED_FILE.name}; unclassified: {len(labels) - len(out)}")


if __name__ == "__main__":
    if "--seed" in sys.argv:
        _seed()
    from ..schemas import Entity, Page
    _known.clear()
    _known.update({"küche": "kitchen", "bad": "bathroom", "zimmer": "bedroom"})
    s = Scene(path="data/x.png", builder="t", tier="parse", covers=["room"], pages=[Page(image="data/x.png", width=1, height=1)],
              entities=[Entity(id="a", type="room", label="Küche"), Entity(id="b", type="room", label=" bad "),
                        Entity(id="c", type="room", label=None, geom=[(0, 0), (.2, 0), (.2, .2), (0, .2)]),
                        Entity(id="d", type="room", label=None, geom=[(.5, .5), (.7, .5), (.7, .7), (.5, .7)]),
                        Entity(id="t", type="fixture", subtype="bathtub", geom=[(.05, .05), (.1, .05), (.1, .1)]),
                        Entity(id="w", type="fixture", subtype="toilet", geom=[(.6, .6), (.62, .6), (.62, .62)])])
    add_room_types(s)
    assert [e.subtype for e in s.entities if e.type == "room"] == ["kitchen", "bathroom", "bathroom", "wc_room"]
    assert "room:kitchen" in s.covers
    u = Scene(path="data/x.png", builder="t", tier="parse", covers=["room"], pages=s.pages,
              entities=[Entity(id="a", type="room", label="Küche"), Entity(id="e", type="room", label=None, geom=[(0, 0), (.1, 0), (.1, .1)])])
    add_room_types(u)  # an empty unlabelled room stays unknown, so no room type is claimed
    assert "room:kitchen" not in u.covers
    t = Scene(path="data/x.png", builder="t", tier="parse", covers=["room"], pages=s.pages,
              entities=[Entity(id="a", type="room", label="Küche"), Entity(id="b", type="room", label="Unknown-Label-XYZ")])
    os.environ.pop("GEMINI_API_KEY", None)
    add_room_types(t)  # one label unknown and no key: no types are claimed
    assert "room:kitchen" not in t.covers
    print("ok: room types")
