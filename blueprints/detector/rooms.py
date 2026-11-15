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
VERSION = 6  # @2: uploads keep the masks no reader's room matches, and get the spaces that rooms stand around;
#              @3: an outlined room names both readers in its ref and takes the mask's score when they agree on its type
#              @4: a reader's box without an outline, inside another room's outline, is dropped as a repeat of it
#              @5: an unnamed space that widens out (a school corridor round a block of rooms) is found whole
#              @6: with no printed room area, an apartment's printed total gives the scale; apartments get a total


def _outlined(scene: Scene) -> bool:
    return MARK in scene.builder and scene.builder.endswith(f"@{VERSION}")


def _gaps(outlines: list) -> list:
    """Spaces that no room covers but rooms stand around on most sides: a stair, a hall, a shaft that neither reader
    named. Sizes are judged against the plan's own typical room, so this needs no scale.
    A space up to 0.8 of a typical room's side wide is found; one that opens out from it, up to 1.4 sides wide (a
    school corridor that widens round a block of rooms), is found whole. A wide space with no narrow part is not:
    closing that wide everywhere also fills the notches along a plan's outside walls.
    ponytail: a missed room at the edge of the plan, open on two sides, is not found. Train the segmenter on stairs,
    halls and unnamed spaces when these matter."""
    from shapely.ops import unary_union

    if len(outlines) < 3:
        return []
    areas = sorted(o.area for o in outlines)
    side = areas[len(areas) // 2] ** 0.5  # the side of a typical room
    wall = 0.1 * side  # outlines stop at the wall faces
    covered = unary_union([o.buffer(wall, join_style="mitre") for o in outlines])

    def free(close):  # the spaces a closing of this radius fills, each wide both ways (an opening drops the strips)
        f = covered.buffer(close * side, join_style="mitre").buffer(-close * side, join_style="mitre").difference(covered)
        wide = f.buffer(-0.15 * side, join_style="mitre").buffer(0.15 * side, join_style="mitre").intersection(f)
        return [g for g in getattr(wide, "geoms", [wide]) if g.geom_type == "Polygon"]

    def room_like(g):  # big enough, and rooms on most sides
        return g.area >= 0.25 * side ** 2 and g.boundary.intersection(covered.buffer(0.2 * wall)).length >= 0.6 * g.length

    wider = free(0.7)
    found = []
    for g in filter(room_like, free(0.4)):
        whole = next((w for w in wider if w.intersects(g)), None)
        found.append(whole if whole is not None and room_like(whole) else g)
    merged = unary_union(found)  # two narrow parts of one corridor grow into the same space
    return [g.simplify(0.1 * wall) for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon"]


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


def _apartment_floors(scene: Scene, rooms: list[Entity], outlines: dict, px) -> list[tuple[Entity, object]]:
    """Each apartment with the floor its indoor rooms cover, in pixels: (apartment, shape). Its rooms are those that name
    it, else those inside its box, else every room when the drawing shows one apartment. An apartment's printed total
    counts the partitions between its rooms, so the room outlines are joined across the walls between them.
    Left out when under four fifths of its rooms have an outline: boxes would make the floor, and so the scale, a guess."""
    from shapely.ops import unary_union

    apartments = [e for e in scene.entities if e.type == "apartment"]
    name = lambda v: str(v).strip().casefold() if v is not None else ""
    shape = lambda i: outlines.get(i) or px(rooms[i].geom)
    out = []
    for apartment in apartments:
        box = px(apartment.geom) if len(apartment.geom) >= 3 else None
        mine = [i for i, r in enumerate(rooms) if name(apartment.label) and name(r.apartment) == name(apartment.label)]
        if not mine and box is not None and box.area > 0 and len(apartments) > 1:
            mine = [i for i in range(len(rooms)) if box.contains(shape(i).representative_point())]
        if not mine and len(apartments) == 1:
            mine = list(range(len(rooms)))
        mine = [i for i in mine if not rooms[i].outdoor]  # balconies and terraces are not in a living area
        if not mine or sum(i in outlines for i in mine) < 0.8 * len(mine):
            continue
        floor = unary_union([shape(i) for i in mine])
        # ponytail: walls taken as up to 6% of the floor's side (0.5 m in a 70 m2 flat) and closed over; this also fills
        # notches that narrow. Measure the wall width from the segmenter's wall mask if areas come out high.
        gap = 0.03 * floor.area ** 0.5
        floor = floor.buffer(gap, join_style=2).buffer(-gap, join_style=2)
        if floor.area > 0:
            out.append((apartment, floor))
    return out


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
    source = f"{len(fits)} printed room area(s)"
    floors_of = _apartment_floors(scene, rooms, outlines, px)
    if not fits:  # no room prints its area: an apartment's printed total over the floor its rooms cover gives the scale
        fits = sorted((a.qty["m2"] / floor.area) ** 0.5 for a, floor in floors_of if a.qty.get("m2"))
        source = f"the printed total area of {len(fits)} apartment(s)"
    m_per_px = fits[len(fits) // 2] if fits else None
    for apartment, floor in floors_of if m_per_px else []:
        area = round(floor.area * m_per_px ** 2, 2)
        if apartment.qty.get("m2"):
            apartment.attrs["outline_m2"] = area  # beside the printed total, so a disagreement shows
        else:
            apartment.qty["m2"] = area
            apartment.attrs |= {"area_estimated": True, "area_from": f"estimated: its rooms' floor at the scale fitted on {source}"}
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
                           "area_from": f"estimated: outline area at the scale fitted on {source}"}
        measure(room, o, m_per_px, openings)
    scale = f"; scale {m_per_px * 1000:.1f} mm per pixel from {source}" if m_per_px else "; no printed room or apartment area, so no scale"
    more = f"; added {masks} room(s) the reader did not list and {len(rooms) - known - masks} unnamed space(s) between rooms" if len(rooms) > known else ""
    _done(scene, seg, f"Outlined {len(pairs)} of {known} rooms{more}{scale}")
    return True


def scale_rooms(scene: Scene, seen: Scene) -> bool:
    """cubicasa: areas for the outlined rooms of add_rooms, from the vision reader's scene of the same plan (seen).
    The detector's rooms, types and counts stay; a reader's room whose box centre lies in an outline lends it its label
    and printed area. The scale is fitted as in outline_rooms: printed room areas, else an apartment's printed total.
    Returns whether any room got an area."""
    from shapely.geometry import Point, Polygon

    from ..schemas import TraceStep

    if any("m2" in e.qty for e in scene.entities if e.type == "room"):
        return False  # scaled before
    W, H = scene.pages[0].width, scene.pages[0].height
    px = lambda pts: Polygon([(x * W, y * H) for x, y in pts]).buffer(0)
    # A sauna is found by its stove, whose box is no room's floor.
    rooms = [e for e in scene.entities if e.type == "room" and len(e.geom) >= 3 and e.label != "sauna (stove)"]
    outlines = {i: o for i, o in ((i, px(r.geom)) for i, r in enumerate(rooms)) if o.geom_type == "Polygon" and o.area > 0}
    read = {}  # room index -> the reader's room whose box centre is inside its outline
    for v in (e for e in seen.entities if e.type == "room" and len(e.geom) >= 3):
        x0, y0, x1, y1 = _box(v.geom)
        centre = Point((x0 + x1) / 2 * W, (y0 + y1) / 2 * H)
        i = next((i for i, o in outlines.items() if i not in read and o.contains(centre)), None)
        if i is not None:
            read[i] = v
    fits = sorted((read[i].qty["m2"] / outlines[i].area) ** 0.5 for i in read if read[i].qty.get("m2"))
    source = f"{len(fits)} printed room area(s)"
    if not fits:
        floors = _apartment_floors(seen, rooms, outlines, px)
        fits = sorted((a.qty["m2"] / floor.area) ** 0.5 for a, floor in floors if a.qty.get("m2"))
        source = f"the printed total area of {len(fits)} apartment(s)"
    if not fits:
        return False
    m_per_px = fits[len(fits) // 2]
    openings = [(px(e.geom), DOOR_H_M if e.type == "door" else WINDOW_H_M)
                for e in scene.entities if e.type in ("door", "window") and len(e.geom) >= 3]
    for i, o in outlines.items():
        room, area, v = rooms[i], round(o.area * m_per_px ** 2, 2), read.get(i)
        if v is not None and v.label and not room.label:
            room.label = v.label
        if v is not None and v.qty.get("m2"):
            room.qty["m2"], room.attrs["outline_m2"] = v.qty["m2"], area  # beside the printed area, so a disagreement shows
        else:
            room.qty["m2"] = area
            room.attrs |= {"area_estimated": True, "area_from": f"estimated: outline area at the scale fitted on {source}"}
        measure(room, o, m_per_px, openings)
    scene.trace.append(TraceStep(step="scene", detail=f"Areas for {len(outlines)} outlined rooms at "
                                 f"{m_per_px * 1000:.1f} mm per pixel, fitted on {source} the vision reader read"))
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
    # cubicasa areas: the reader prints 9 m2 inside the L (90000 px2) -> 0.01 m per px; the living room's 400 x 400 px
    # is 16 m2 and the kitchen box 9 m2, estimated; the sauna stove's box gets none. Types and counts stay.
    s.entities[2].label = "sauna (stove)"
    seen_s = Scene(path="data/x.png", builder="vision:x@6", tier="detect", covers=["room"], pages=[page], entities=[
        Entity(id="v0", type="room", subtype="bedroom", label="MH", geom=box(0.12, 0.12, 0.38, 0.28), qty={"m2": 9.0})])
    assert scale_rooms(s, seen_s) and not scale_rooms(s, seen_s)  # the second time adds nothing
    bed, kitchen, sauna, living = s.entities[:4]
    assert (bed.label, bed.qty["m2"], bed.attrs["outline_m2"], kitchen.qty["m2"], living.qty["m2"]) == ("MH", 9.0, 9.0, 9.0, 16.0)
    assert kitchen.attrs["area_estimated"] and not sauna.qty and (count("bedroom"), count("kitchen")) == (1, 1)
    flat_s = seen_s.model_copy(update={"entities": [Entity(id="a", type="apartment", geom=box(0, 0, 1, 1), qty={"m2": 25.0})]})
    s2 = Scene(path="data/x.png", builder="b", tier="detect", covers=[], pages=[page], entities=[det(1, "kitchen", 0.6, 0.6, 0.9, 0.9),
               Entity(id="l", type="room", subtype="living", geom=box(0.1, 0.1, 0.5, 0.5))])
    assert scale_rooms(s2, flat_s) and [e.qty["m2"] for e in s2.entities] == [9.0, 16.0]  # no room printed: 25 m2 total

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
    # No room prints its area, but the apartment prints 29 m2 over rooms that cover 400 x 500 + 300 x 300 px: 0.01 m per
    # px again, so A is 20 m2 and B 9 m2, both estimated, with perimeters; the unoutlined box C is outside the apartment.
    flat = lambda **more: Entity(id="vis-apartment-0", type="apartment", label="A", geom=box(0.05, 0.05, 0.85, 0.65), conf=0.7, **more)
    u = upload(printed=False)
    u.entities.append(flat(qty={"m2": 29.0}))
    for e in u.entities[:2]:
        e.apartment = "A"
    assert outline_rooms(u, Path("x.png"), seen)
    a, b, c = u.entities[:3]
    assert (a.qty["m2"], a.qty["m"], b.qty["m2"], b.qty["m"]) == (20.0, 18.0, 9.0, 12.0) and a.attrs["area_estimated"] and not c.qty
    assert "printed total area of 1 apartment(s)" in a.attrs["area_from"] and "10.0 mm per pixel from the printed total area of 1 apartment(s)" in u.trace[-1].detail
    assert u.entities[-1].qty == {"m2": 29.0} and u.entities[-1].attrs["outline_m2"] == 29.0
    # One apartment, unnamed, rooms that do not say which apartment they are in: every indoor room is its floor.
    u = upload(printed=False)
    u.entities = u.entities[:2] + [u.entities[3], flat(qty={"m2": 29.0}).model_copy(update={"label": None})]
    assert outline_rooms(u, Path("x.png"), seen) and [e.qty["m2"] for e in u.entities[:2]] == [20.0, 9.0]
    # Printed room areas still set the scale when there are any; the apartment then gets its total, estimated or checked.
    u = upload()
    u.entities.append(flat())
    for e in u.entities[:2]:
        e.apartment = "A"
    assert outline_rooms(u, Path("x.png"), seen) and u.entities[-1].qty == {"m2": 29.0} and u.entities[-1].attrs["area_estimated"]
    assert "from 1 printed room area(s)" in u.trace[-1].detail
    # Under four fifths of the apartment's rooms outlined: its total gives no scale (the floor would be a guess).
    u = upload(printed=False)
    u.entities.append(flat(qty={"m2": 29.0}))
    for e in u.entities[:3]:
        e.apartment = "A"
    assert outline_rooms(u, Path("x.png"), seen) and not any(e.qty.get("m2") for e in u.entities[:3]) and "no scale" in u.trace[-1].detail
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
    # A corridor narrow at the top that widens past 0.8 of a room's side lower down: found whole, not just its neck.
    from shapely.geometry import box as sbox
    wing = [sbox(100, y, 300, y + 200) for y in (100, 300, 500, 700)] + [sbox(420, 100, 620, 300), sbox(420, 300, 620, 500),
            sbox(550, 500, 750, 700), sbox(550, 700, 750, 900), sbox(300, 0, 420, 100), sbox(300, 900, 550, 1000)]
    (hall,) = _gaps(wing)
    assert hall.bounds[1] < 150 and hall.bounds[3] > 850 and hall.bounds[2] > 520, hall.bounds
    # a scene outlined by an older version is outlined again, without doubling what was added
    u.builder = u.builder.replace(f"@{VERSION}", "@1")
    assert outline_rooms(u, Path("y.png"), out(*(found("other", b) for b in ring[:3]))) and sum(e.attrs.get("unnamed", False) for e in u.entities) == 1
    assert u.builder.count(MARK) == 1 and _outlined(u)
    print("ok")
