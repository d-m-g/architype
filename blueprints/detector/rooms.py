"""Lane 4. Room outlines for raster scenes, from the segmenter in segment.py (Modal app blueprints-rooms).

add_rooms():     cubicasa. The detector's room boxes keep their type and their count (tuned against gold); each takes
                 the outline of the mask it overlaps. Masks of the types the detector does not find are added as rooms.
outline_rooms(): uploads. The vision reader's room boxes take outlines; rooms that print their area give the scale,
                 which turns every other outline into m2, a perimeter and a wall area (outlines.measure).

Both take the segmenter's output as an argument, so they run without Modal; segment() is the one remote call.
Check:  uv run --extra parsers python -m blueprints.detector.rooms
"""
from pathlib import Path

from .. import costs
from ..outlines import DOOR_H_M, WINDOW_H_M, measure
from ..schemas import Entity, Scene

EXTRA = ("living", "corridor", "storage", "other")  # room types only the segmenter finds


def segment(file: Path) -> dict:
    """{"rooms": [{"cls", "score", "polygon": [[x, y] 0..1]}], "width", "height", "model", "gpu", "seconds"}."""
    try:
        import modal
        return modal.Cls.from_name("blueprints-rooms", "Segmenter")().segment.remote(file.read_bytes())
    except Exception as e:  # not deployed, no credentials, a failed call: the scene stays as it was
        raise NotImplementedError(f"room segmenter unavailable: {type(e).__name__}: {e}") from None


def _box(pts) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _iou(a, b) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-12)


def _pair(rooms: list[Entity], found: list[dict], least: float) -> dict[int, int]:
    """room index -> outline index: a room box goes with the outline whose bounding box it overlaps most (IoU >=
    least), each used once, best pairs first. The detector's room box is the bounding box of the room's outline."""
    ious = sorted(((_iou(_box(r.geom), _box(f["polygon"])), i, j) for i, r in enumerate(rooms) for j, f in enumerate(found)), reverse=True)
    pairs: dict[int, int] = {}
    for iou, i, j in ious:
        if iou >= least and i not in pairs and j not in pairs.values():
            pairs[i] = j
    return pairs


def _geom(polygon) -> list[tuple[float, float]]:
    return [(min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)) for x, y in polygon]


def _done(scene: Scene, seg: dict, detail: str) -> None:
    step = costs.gpu_step("scene", seg["gpu"], seg["seconds"])
    step.detail = detail
    scene.trace.append(step)
    scene.builder += f"{MARK}{seg['model']}@1"


MARK = "+outlines:"  # in Scene.builder once a scene has its outlines, so a cached scene is not segmented again


def add_rooms(scene: Scene, file: Path, seg: dict | None = None) -> None:
    """cubicasa: outlines for the detector's rooms, and the rooms of types it does not find. No count of a type the
    detector covers changes, and no areas: these rasters have no scale."""
    if MARK in scene.builder:
        return
    seg = seg or segment(file)
    rooms = [e for e in scene.entities if e.type == "room" and len(e.geom) >= 3]
    pairs = _pair(rooms, seg["rooms"], 0.5)
    for i, j in pairs.items():
        rooms[i].geom = _geom(seg["rooms"][j]["polygon"])
    extra = [(j, f) for j, f in enumerate(seg["rooms"]) if j not in pairs.values() and f["cls"] in EXTRA]
    scene.entities += [Entity(id=f"seg-{j}", type="room", subtype=f["cls"], geom=_geom(f["polygon"]), conf=f["score"], ref=f"seg:{j}")
                       for j, f in extra]
    scene.covers += [k for k in (f"room:{t}" for t in EXTRA) if k not in scene.covers]
    _done(scene, seg, f"Outlined {len(pairs)} of {len(rooms)} detected rooms; found {len(extra)} more (living, corridor, storage, other)")


def outline_rooms(scene: Scene, file: Path, seg: dict | None = None) -> bool:
    """Uploads: the reader's room boxes become outlines. With at least one printed room area the outlines get a scale
    (the median of sqrt(printed m2 / outline px2), as parsers/pdf.py fits it), so rooms without a printed area get an
    estimated one, and every outlined room a perimeter and a wall area. Returns whether any room was outlined."""
    from shapely.geometry import Polygon

    if MARK in scene.builder:
        return False
    seg = seg or segment(file)
    W, H = scene.pages[0].width, scene.pages[0].height
    rooms = [e for e in scene.entities if e.type == "room" and len(e.geom) >= 3]
    # ponytail: 0.3, looser than add_rooms: a vision model's boxes are rough. A label point inside the outline would
    # be the surer test if the reader gave one.
    pairs = _pair(rooms, seg["rooms"], 0.3)
    if not pairs:
        return False

    def px(pts):
        poly = Polygon([(x * W, y * H) for x, y in pts]).buffer(0)
        return max(poly.geoms, key=lambda g: g.area) if poly.geom_type == "MultiPolygon" else poly

    outlines = {i: px(seg["rooms"][j]["polygon"]) for i, j in pairs.items()}
    outlines = {i: o for i, o in outlines.items() if o.geom_type == "Polygon" and o.area > 0}
    fits = sorted((rooms[i].qty["m2"] / o.area) ** 0.5 for i, o in outlines.items() if rooms[i].qty.get("m2"))
    m_per_px = fits[len(fits) // 2] if fits else None
    openings = [(px(e.geom), DOOR_H_M if e.type == "door" else WINDOW_H_M)
                for e in scene.entities if e.type in ("door", "window") and len(e.geom) >= 3]
    for i, o in outlines.items():
        room = rooms[i]
        room.geom = _geom(seg["rooms"][pairs[i]]["polygon"])
        if not m_per_px:
            continue
        area = round(o.area * m_per_px ** 2, 2)
        if room.qty.get("m2"):
            room.attrs["outline_m2"] = area  # beside the printed area, so a disagreement shows
        else:
            room.qty["m2"] = area
            room.attrs |= {"area_estimated": True,
                           "area_from": f"estimated: outline area at the scale fitted on {len(fits)} printed room area(s)"}
        measure(room, o, m_per_px, openings)
    scale = f"; scale {m_per_px * 1000:.1f} mm per pixel from {len(fits)} printed room area(s)" if m_per_px else "; no printed room area, so no scale"
    _done(scene, seg, f"Outlined {len(outlines)} of {len(rooms)} rooms{scale}")
    return bool(outlines)


if __name__ == "__main__":
    from ..schemas import Page

    box = lambda x0, y0, x1, y1: [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    found = lambda cls, polygon, score=0.9: {"cls": cls, "score": score, "polygon": [list(p) for p in polygon]}
    out = lambda *rooms: {"rooms": list(rooms), "width": 1000, "height": 1000, "model": "rooms-1", "gpu": "T4", "seconds": 0.5}
    L = [(0.1, 0.1), (0.4, 0.1), (0.4, 0.3), (0.25, 0.3), (0.25, 0.5), (0.1, 0.5)]
    page = Page(image="data/x.png", width=1000, height=1000)

    # cubicasa: a bedroom box takes the L-shaped outline over it, whatever class the mask has; the sauna stove and the
    # kitchen (no mask over it) stay boxes; a bedroom mask elsewhere is not added, a living room is
    det = lambda i, sub, *b: Entity(id=f"det-{i}", type="room", subtype=sub, geom=box(*b), conf=0.8, ref=f"det:{i}")
    s = Scene(path="data/x.png", builder="fixtures-6+all-13@2", tier="detect", covers=["room:bedroom", "room:kitchen", "room:sauna"], pages=[page],
              entities=[det(0, "bedroom", 0.11, 0.1, 0.4, 0.49), det(1, "kitchen", 0.6, 0.6, 0.9, 0.9), det(2, "sauna", 0.3, 0.32, 0.33, 0.35)])
    add_rooms(s, Path("x.png"), out(found("other", L), found("living", box(0.5, 0.1, 0.9, 0.5)), found("bedroom", box(0.1, 0.6, 0.4, 0.9)),
                                    found("kitchen", box(0.6, 0.6, 0.7, 0.7))))
    count = lambda sub: sum(e.subtype == sub for e in s.entities)
    assert (count("bedroom"), count("kitchen"), count("sauna"), count("living"), count("other")) == (1, 1, 1, 1, 0)
    assert s.entities[0].geom == L and s.entities[1].geom == box(0.6, 0.6, 0.9, 0.9) and all("m2" not in e.qty for e in s.entities)
    assert {"room:living", "room:corridor", "room:storage", "room:other"} <= set(s.covers) and "room" not in s.covers
    assert s.builder.endswith("+outlines:rooms-1@1") and s.trace[-1].gpu_s == 0.5
    Scene.model_validate(s.model_dump())
    add_rooms(s, Path("x.png"), out(found("living", box(0, 0, 0.05, 0.05))))  # already outlined: not segmented again
    assert count("living") == 1 and len(s.trace) == 1

    # upload: A prints 20 m2 over a 400 x 500 px outline -> 0.01 m per px; B gets 3 x 3 m = 9 m2, a 12 m perimeter and
    # 12 x 2.5 m of wall less a 0.9 x 2.0 m door; C has no outline and stays a box
    def upload(printed=True):
        vis = lambda i, b, **more: Entity(id=f"vis-room-{i}", type="room", geom=box(*b), conf=0.7, **more)
        return Scene(path="uploads/x.png", builder="fixtures-6:tiled@2+vision:x@5", tier="detect", covers=["room", "door"], pages=[page], entities=[
            vis(0, (0.09, 0.1, 0.5, 0.62), qty={"m2": 20.0} if printed else {}), vis(1, (0.52, 0.12, 0.8, 0.4)), vis(2, (0.1, 0.7, 0.2, 0.8)),
            Entity(id="det-0", type="door", geom=box(0.6, 0.39, 0.69, 0.41), conf=0.9)])
    seen = out(found("bedroom", box(0.1, 0.1, 0.5, 0.6)), found("other", box(0.5, 0.1, 0.8, 0.4)))
    u = upload()
    assert outline_rooms(u, Path("x.png"), seen)
    a, b, c = u.entities[:3]
    assert a.qty == {"m2": 20.0, "m": 18.0, "wall_m2": 45.0} and a.attrs["outline_m2"] == 20.0 and "area_estimated" not in a.attrs
    assert b.qty == {"m2": 9.0, "m": 12.0, "wall_m2": 28.2} and b.attrs["area_estimated"] and b.attrs["wall_openings"] == 1
    assert list(b.qty)[0] == "m2" and b.geom == box(0.5, 0.1, 0.8, 0.4) and c.geom == box(0.1, 0.7, 0.2, 0.8) and not c.qty
    assert "10.0 mm per pixel from 1 printed" in u.trace[-1].detail
    Scene.model_validate(u.model_dump())
    assert not outline_rooms(u, Path("x.png"), seen) and len(u.trace) == 1  # already outlined
    # no printed area: outlines only; nothing overlapping: nothing changes
    u = upload(printed=False)
    assert outline_rooms(u, Path("x.png"), seen) and not any(e.qty for e in u.entities) and u.entities[1].geom == box(0.5, 0.1, 0.8, 0.4)
    u = upload()
    assert not outline_rooms(u, Path("x.png"), out(found("other", box(0.85, 0.85, 0.99, 0.99)))) and u.builder.endswith("@5")
    print("ok")
