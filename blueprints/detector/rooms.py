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
    scene.builder = scene.builder.split(MARK)[0] + f"{MARK}{seg['model']}@{VERSION}"


MARK = "+outlines:"  # in Scene.builder once a scene has its outlines, so a cached scene is not segmented again
VERSION = 4  # @2: uploads keep the masks no reader's room matches, and get the spaces that rooms stand around;
#              @3: an outlined room names both readers in its ref and takes the mask's score when they agree on its type
#              @4: a reader's box without an outline, inside another room's outline, is dropped as a repeat of it


def _outlined(scene: Scene) -> bool:
    return MARK in scene.builder and scene.builder.endswith(f"@{VERSION}")


def _gaps(outlines: list) -> list:
    """Spaces that no room covers but rooms stand around on most sides: a stair, a hall, a shaft that neither reader
    named. Sizes are judged against the plan's own typical room, so this needs no scale.
    ponytail: a missed room at the edge of the plan, open on two sides, is not found; nor is a gap wider than 0.8 of a
    typical room's side. Train the segmenter on stairs and unnamed spaces when these matter."""
    from shapely.ops import unary_union

    if len(outlines) < 3:
        return []
    areas = sorted(o.area for o in outlines)
    side = areas[len(areas) // 2] ** 0.5  # the side of a typical room
    wall = 0.1 * side  # outlines stop at the wall faces
    covered = unary_union([o.buffer(wall, join_style="mitre") for o in outlines])
    free = covered.buffer(0.4 * side, join_style="mitre").buffer(-0.4 * side, join_style="mitre").difference(covered)
    # A real space is wide both ways (opening it drops the strips and pockets a closing leaves) and has rooms on most sides.
    wide = free.buffer(-0.15 * side, join_style="mitre").buffer(0.15 * side, join_style="mitre").intersection(free)
    return [g.simplify(0.1 * wall) for g in getattr(wide, "geoms", [wide])
            if g.geom_type == "Polygon" and g.area >= 0.25 * side ** 2
            and g.boundary.intersection(covered.buffer(0.2 * wall)).length >= 0.6 * g.length]


def add_rooms(scene: Scene, file: Path, seg: dict | None = None) -> None:
    """cubicasa: outlines for the detector's rooms, and the rooms of types it does not find. No count of a type the
    detector covers changes, and no areas: these rasters have no scale."""
    if _outlined(scene):
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
    from shapely.ops import unary_union

    if _outlined(scene):
        return False
    seg = seg or segment(file)
    W, H = scene.pages[0].width, scene.pages[0].height
    scene.entities = [e for e in scene.entities if not (e.ref or "").startswith("seg:")]  # an older version's additions
    rooms = [e for e in scene.entities if e.type == "room" and len(e.geom) >= 3]
    # ponytail: 0.3, looser than add_rooms: a vision model's boxes are rough. A label point inside the outline would
    # be the surer test if the reader gave one.
    pairs = _pair(rooms, seg["rooms"], 0.3)

    def px(pts):
        poly = Polygon([(x * W, y * H) for x, y in pts]).buffer(0)
        return max(poly.geoms, key=lambda g: g.area) if poly.geom_type == "MultiPolygon" else poly

    def outlined(pairs):
        found = {i: px(seg["rooms"][j]["polygon"]) for i, j in pairs.items()}
        return {i: o for i, o in found.items() if o.geom_type == "Polygon" and o.area > 0}

    outlines = outlined(pairs)
    # A reader's box left without an outline that lies mostly inside another room's outline is that room listed again
    # in parts (a church hall as its nave and side zones): dropped, not drawn over it. Only when it has no label or the
    # same type, so a kitchen box inside a mask that merged kitchen and living room stays a room.
    def again(r):
        box = px(r.geom)
        return box.area > 0 and any(box.intersection(o).area >= 0.6 * box.area and (not r.label or r.subtype == rooms[k].subtype)
                                    for k, o in outlines.items())
    dup = {id(r) for i, r in enumerate(rooms) if i not in outlines and again(r)}
    if dup:  # dropping rooms that got no outline leaves every other pair as it was
        scene.entities = [e for e in scene.entities if id(e) not in dup]
        rooms = [r for r in rooms if id(r) not in dup]
        pairs = _pair(rooms, seg["rooms"], 0.3)
        outlines = outlined(pairs)
    known = len(rooms)

    def add(outline, **fields) -> None:
        rooms.append(Entity(type="room", geom=_geom([(x / W, y / H) for x, y in outline.exterior.coords[:-1]]), **fields))
        outlines[len(rooms) - 1] = outline

    # Nothing the readers saw goes missing. A mask that no room of the reader matches is a room it skipped (a dining
    # corner, a porch), unless it lies over rooms already there.
    have = unary_union([outlines.get(i) or px(r.geom) for i, r in enumerate(rooms)])
    for j, f in enumerate(seg["rooms"]):
        o = px(f["polygon"])
        if j not in pairs.values() and f["score"] >= 0.5 and o.geom_type == "Polygon" and 0.3 * o.area > o.intersection(have).area:
            add(o, id=f"seg-{j}", subtype=f["cls"], outdoor=f["cls"] == "balcony", conf=f["score"], ref=f"seg:{j}")
            have = have.union(o)
    masks = len(rooms) - known
    # And a space that rooms stand around but no reader named (a stair) is put up for review, at a doubtful score.
    # Floor by floor: the strip between two floors drawn side by side has rooms on both sides and is no space.
    floors = [px(e.geom) for e in scene.entities if e.type == "level" and len(e.geom) >= 3]
    by_floor: dict[int, list] = {}
    for o in [outlines.get(i) or px(r.geom) for i, r in enumerate(rooms)]:
        by_floor.setdefault(next((n for n, f in enumerate(floors) if f.contains(o.representative_point())), -1), []).append(o)
    for k, gap in enumerate(g for group in by_floor.values() for g in _gaps(group)):
        add(gap, id=f"seg-gap-{k}", subtype="other", label="Unnamed space", conf=0.5, ref=f"seg:gap{k}",
            attrs={"unnamed": True})
    scene.entities += rooms[known:]
    if not outlines:
        return False

    fits = sorted((rooms[i].qty["m2"] / o.area) ** 0.5 for i, o in outlines.items() if rooms[i].qty.get("m2"))
    m_per_px = fits[len(fits) // 2] if fits else None
    openings = [(px(e.geom), DOOR_H_M if e.type == "door" else WINDOW_H_M)
                for e in scene.entities if e.type in ("door", "window") and len(e.geom) >= 3]
    for i, o in outlines.items():
        room = rooms[i]
        if i in pairs:  # the reader's room, the segmenter's outline: the room says so, and how sure the outline is
            f = seg["rooms"][pairs[i]]
            room.geom = _geom(f["polygon"])
            room.attrs["outline_score"] = round(f["score"], 2)
            if room.ref:
                room.ref = f"{room.ref.split(' + seg:')[0]} + seg:{pairs[i]}"
            if f["cls"] == room.subtype:  # two readers that call it the same kind of room
                room.conf = max(room.conf, f["score"])
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
    more = f"; added {masks} room(s) the reader did not list and {len(rooms) - known - masks} unnamed space(s) between rooms" if len(rooms) > known else ""
    _done(scene, seg, f"Outlined {len(pairs)} of {known} rooms{more}{scale}")
    return True


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
    assert s.builder.endswith(f"+outlines:rooms-1@{VERSION}") and s.trace[-1].gpu_s == 0.5
    Scene.model_validate(s.model_dump())
    add_rooms(s, Path("x.png"), out(found("living", box(0, 0, 0.05, 0.05))))  # already outlined: not segmented again
    assert count("living") == 1 and len(s.trace) == 1

    # upload: A prints 20 m2 over a 400 x 500 px outline -> 0.01 m per px; B gets 3 x 3 m = 9 m2, a 12 m perimeter and
    # 12 x 2.5 m of wall less a 0.9 x 2.0 m door; C has no outline and stays a box
    def upload(printed=True):
        vis = lambda i, b, **more: Entity(id=f"vis-room-{i}", type="room", geom=box(*b), conf=0.7, **more)
        return Scene(path="uploads/x.png", builder="fixtures-6:tiled@2+vision:x@5", tier="detect", covers=["room", "door"], pages=[page], entities=[
            vis(0, (0.09, 0.1, 0.5, 0.62), qty={"m2": 20.0} if printed else {}, subtype="bedroom", ref="gem:0"),
            vis(1, (0.52, 0.12, 0.8, 0.4), subtype="kitchen", ref="gem:1"), vis(2, (0.1, 0.7, 0.2, 0.8)),
            Entity(id="det-0", type="door", geom=box(0.6, 0.39, 0.69, 0.41), conf=0.9)])
    seen = out(found("bedroom", box(0.1, 0.1, 0.5, 0.6)), found("other", box(0.5, 0.1, 0.8, 0.4)))
    u = upload()
    assert outline_rooms(u, Path("x.png"), seen)
    a, b, c = u.entities[:3]
    assert a.qty == {"m2": 20.0, "m": 18.0, "wall_m2": 45.0} and a.attrs["outline_m2"] == 20.0 and "area_estimated" not in a.attrs
    assert b.qty == {"m2": 9.0, "m": 12.0, "wall_m2": 28.2} and b.attrs["area_estimated"] and b.attrs["wall_openings"] == 1
    assert list(b.qty)[0] == "m2" and b.geom == box(0.5, 0.1, 0.8, 0.4) and c.geom == box(0.1, 0.7, 0.2, 0.8) and not c.qty
    assert "10.0 mm per pixel from 1 printed" in u.trace[-1].detail
    # A is a bedroom to both readers, so it takes the mask's score; B is a kitchen to one and "other" to the other
    assert (a.conf, a.ref, a.attrs["outline_score"]) == (0.9, "gem:0 + seg:0", 0.9) and (b.conf, b.ref) == (0.7, "gem:1 + seg:1")
    Scene.model_validate(u.model_dump())
    assert not outline_rooms(u, Path("x.png"), seen) and len(u.trace) == 1  # already outlined
    # no printed area: outlines only; nothing overlapping: nothing changes
    u = upload(printed=False)
    assert outline_rooms(u, Path("x.png"), seen) and not any(e.qty for e in u.entities) and u.entities[1].geom == box(0.5, 0.1, 0.8, 0.4)
    u = upload()
    assert not outline_rooms(u, Path("x.png"), out()) and u.builder.endswith("@5")  # the segmenter saw nothing
    # A mask where the reader listed no room is a room it skipped: added, with an estimated area. One lying over a
    # room that is already there, or a doubtful one, is not.
    u = upload()
    assert outline_rooms(u, Path("x.png"), out(*seen["rooms"], found("living", box(0.5, 0.5, 0.9, 0.9)),
                                               found("other", box(0.12, 0.12, 0.3, 0.3)), found("other", box(0.82, 0.1, 0.99, 0.4), 0.3)))
    added = [e for e in u.entities if e.id.startswith("seg-") and not e.attrs.get("unnamed")]
    assert [(e.subtype, e.qty["m2"], e.attrs["area_estimated"]) for e in added] == [("living", 16.0, True)], added
    assert "added 1 room(s) the reader did not list" in u.trace[-1].detail
    # A hall the reader also listed in parts: the unlabelled box inside the hall's outline goes, and so does one of the
    # same type that pokes out a little; a labelled kitchen of another type inside the same mask stays a room.
    vis = lambda i, b, **more: Entity(id=f"vis-room-{i}", type="room", geom=box(*b), conf=0.7, **more)
    u = Scene(path="uploads/h.png", builder="fixtures-6:tiled@2+vision:x@5", tier="detect", covers=["room"], pages=[page],
              entities=[vis(0, (0.1, 0.1, 0.7, 0.5), subtype="other"), vis(1, (0.1, 0.1, 0.25, 0.5)),
                        vis(2, (0.5, 0.12, 0.7, 0.55), subtype="other", label="Apse"), vis(3, (0.3, 0.2, 0.4, 0.3), subtype="kitchen", label="K")])
    assert outline_rooms(u, Path("h.png"), out(found("other", box(0.1, 0.1, 0.7, 0.5))))
    assert [e.id for e in u.entities] == ["vis-room-0", "vis-room-3"], [e.id for e in u.entities]
    # A stair that rooms stand around on three sides and no reader named: put up for review, not left out.
    ring = [box(0.1, 0.1, 0.7, 0.3), box(0.1, 0.3, 0.3, 0.5), box(0.1, 0.5, 0.7, 0.7), box(0.72, 0.1, 0.9, 0.7)]
    u = Scene(path="uploads/y.png", builder="fixtures-6:tiled@2+vision:x@5", tier="detect", covers=["room"], pages=[page],
              entities=[Entity(id=f"vis-room-{i}", type="room", geom=b, conf=0.7) for i, b in enumerate(ring)])
    assert outline_rooms(u, Path("y.png"), out(*(found("other", b) for b in ring[:3])))
    gap = [e for e in u.entities if e.attrs.get("unnamed")]
    assert len(gap) == 1 and (gap[0].label, gap[0].conf) == ("Unnamed space", 0.5)
    x0, y0, x1, y1 = _box(gap[0].geom)
    assert 0.3 < x0 < 0.36 and 0.3 < y0 < 0.34 and 0.64 < x1 <= 0.72 and 0.46 < y1 < 0.5, gap[0].geom
    Scene.model_validate(u.model_dump())
    # a scene outlined by an older version is outlined again, without doubling what was added
    u.builder = u.builder.replace(f"@{VERSION}", "@1")
    assert outline_rooms(u, Path("y.png"), out(*(found("other", b) for b in ring[:3]))) and sum(e.attrs.get("unnamed", False) for e in u.entities) == 1
    assert u.builder.count(MARK) == 1 and _outlined(u)
    print("ok")
