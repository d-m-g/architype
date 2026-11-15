"""Lane 3. A DXF upload that is not in the Swiss layer scheme -> Scene with room outlines, areas and wall areas.

Two ways to find rooms, by what the layer names say (any case, several languages):
  1  a room layer (ROOM, RAUM, A-AREA, ZONE, TILA...) holding closed polylines or hatches: those are the rooms.
  2  wall layers (WALL, WAND, MUR, SEIN...): the wall mass, with the doors and windows on their own layers or in
     blocks painted in as wall, encloses the rooms (outlines.faces). Refused when the walls leave more than a
     tenth of the plan open, so a leaking room is rarely dropped in silence. Rooms no wall divides (an open
     plan) come out as one space, marked with attrs.rooms_inside.
Labels are the TEXT / MTEXT standing inside a room. Units come from $INSUNITS, else from which of m, mm or cm gives
rooms a plausible size. Anything else raises NotImplementedError, so the upload goes to the raster reader.

Covers rooms only: doors and windows are used to close the rooms and for the wall area, not counted.

Check:  uv run --extra parsers python -m blueprints.parsers.dxf
"""
import math
import re
import statistics
from pathlib import Path

from .. import outlines
from ..schemas import Entity, Scene

_W = r"(?<![a-z])"  # short words only at a word start: "tur" is a door, "fixture" is not
ROOM = re.compile(rf"room|raum|zimmer|space|{_W}(area|zone|pi[eè]ce|local|stanz|vano|tila|huone|rum)", re.I)
LABELS = re.compile(r"text|txt|iden|tag|name|label|schrift|num", re.I)  # a room layer of tags, not outlines
WALL = re.compile(rf"wall|wand|w[aä]nde|{_W}(mur|pared|sein|v[aä]gg)", re.I)
DOOR = re.compile(rf"door|{_W}(t[uü]e?r|porte|porta|puerta|ovi|ovet|d[oö]rr)", re.I)
WINDOW = re.compile(r"window|fenster|fen[eê]tre|finestr|ventana|ikkuna|f[oö]nster", re.I)
M_PER_UNIT = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}  # $INSUNITS: inch, foot, mm, cm, m
NUMBER = re.compile(r"[A-Za-z]{0,2}\d[\w.\-/]*")
AREA = re.compile(r"[\d.,]+\s*(m²|m2|qm|sq\.?\s?ft|sf)", re.I)
# ponytail: sizes a room is taken to have. Double-line walls thicker than MIN_WIDTH_M become slivers read as rooms,
# and closets narrower than it are dropped; tune on real files when one of those shows up.
MIN_WIDTH_M, MIN_AREA_M2, PLAUSIBLE_M2 = 0.3, 0.5, (4, 60)
THIN_M, CLOSE_M = 0.002, 0.02  # the thickness given to a wall drawn as a line; wall ends this close are joined
ACROSS_M = 0.2  # an opening belongs to a room whose outline passes within this of it, across the wall
MIN_ENCLOSED = 0.9  # tier 2: rooms plus walls must cover this share of the plan's footprint
# Tier 2 rooms: 88% of the numbered rooms of 40 Swiss train plans came out as their drawn outline (IoU >= 0.8, area
# within 0.2%); most of the rest are open-plan rooms no wall divides, which come out as one space.
WALLS_CONF = 0.88


def _rings(e, tol: float):
    """An entity's outlines as (points, closes): polylines, lines, arcs, circles, splines and hatch boundaries."""
    from ezdxf import path
    try:
        paths = list(path.from_hatch(e)) if e.dxftype() == "HATCH" else [path.make_path(e)]
    except (TypeError, ValueError, AttributeError):  # texts, dimensions, 3D faces: nothing to outline
        return
    for p in paths:
        pts = [(v.x, v.y) for v in p.flattening(tol)]
        if len(pts) >= 2:
            yield pts, len(pts) >= 4 and (p.is_closed or bool(getattr(e, "closed", False)))


def _polygon(pts):
    from shapely.geometry import Polygon
    p = Polygon(pts).buffer(0)
    if p.geom_type == "MultiPolygon":
        p = max(p.geoms, key=lambda g: g.area)
    return p if p.geom_type == "Polygon" and p.area > 0 else None


def _parts(entities, kind_of, kind=None, depth=0):
    """(kind, entity) for everything that is a wall, door or window: by its layer, or by the block it belongs to."""
    for e in entities:
        k = kind or kind_of(e.dxf.layer)
        if e.dxftype() == "INSERT":
            k = k or ("door" if DOOR.search(e.dxf.name) else "window" if WINDOW.search(e.dxf.name) else None)
            if k and depth < 3:
                if k == "window":
                    yield "window-block", e
                else:
                    yield from _parts(e.virtual_entities(), kind_of, k, depth + 1)
        elif k:
            yield k, e


def _outlined(msp, layers: set, tol: float) -> list:
    """Tier 1: (outline, handle) for each closed shape on a room layer. A hatch and a polyline drawn on the same
    boundary are one room; an outline around several others (an apartment, a zone) is not a room."""
    from shapely.ops import unary_union

    polys, seen = [], set()
    for e in msp:
        if e.dxf.layer in layers:
            for pts, closed in _rings(e, tol):
                p = _polygon(pts) if closed else None
                key = p and tuple(round(v / (20 * tol)) for v in (*p.bounds, p.area ** 0.5))
                if p and key not in seen:
                    seen.add(key)
                    polys.append((p, e.dxf.handle))
    rooms = []
    for p, handle in polys:
        inner = [q for q, _ in polys if q is not p and q.within(p)]
        if inner and unary_union(inner).area > 0.6 * p.area:
            continue
        rooms.append((p.difference(unary_union(inner)) if inner else p, f"dxf:{handle}"))  # a hole is its own outline
    return rooms


def _wall_mass(msp, kind_of, tol: float, thin: float) -> list:
    from shapely.geometry import LineString

    mass = []
    for kind, e in _parts(msp, kind_of):
        for pts, closed in _rings(e, tol) if kind == "wall" else ():
            # ponytail: a closed outline on a wall layer is taken as a piece of wall; a file that traces its
            # rooms on the wall layer would come out with rooms and walls swapped (the footprint check refuses it)
            shape = _polygon(pts) if closed else LineString(pts).buffer(thin / 2)
            if shape is not None:
                mass.append(shape)
    return mass


def _openings(msp, kind_of, tol: float, strip: float) -> tuple[list, list]:
    """Boxes as (shape, height in metres): a window's box, a door's frame box. Swings: for a door drawn only as a
    swing arc, the two lines from its hinge to the ends of the arc (one is the closed leaf, which spans the doorway)."""
    from shapely.geometry import LineString, MultiPoint, Point
    from shapely.ops import unary_union

    out, arcs, swings = [], [], []
    for kind, e in _parts(msp, kind_of):
        if kind == "wall":
            continue
        if kind == "window-block":  # the symbol sits in the wall, so its box is the opening
            pts = [p for part in e.virtual_entities() for ring, _ in _rings(part, tol) for p in ring]
            box = MultiPoint(pts).minimum_rotated_rectangle if len(pts) >= 3 else None
            if box is not None and box.geom_type == "Polygon":
                out.append((box, outlines.WINDOW_H_M))
        elif e.dxftype() == "ARC" and kind == "door":
            arcs.append(e)
        else:
            for pts, closed in _rings(e, tol):
                if closed and len(set(pts)) == 4 and (p := _polygon(pts)):
                    out.append((p, outlines.DOOR_H_M if kind == "door" else outlines.WINDOW_H_M))
    frames = unary_union([s for s, h in out if h == outlines.DOOR_H_M]).buffer(2 * strip)
    for e in arcs:
        c = (e.dxf.center.x, e.dxf.center.y)
        if not frames.contains(Point(c)):  # hinged on a frame box already drawn: the box closes that doorway
            swings.append(tuple(LineString([c, (p.x, p.y)]) for p in (e.start_point, e.end_point)))
    return out, swings


def swing_lines(swings: list, strip: float) -> list:
    """Both lines of every swing as shapes to paint in as wall, 2 * strip thick and that much longer: a swing is
    drawn along the wall face, a few mm off the wall itself.
    ponytail: both, since which end is the closed leaf is not drawn (keeping only the end nearer a wall matched 77%
    of rooms against 87%). The open leaf is a slit enclosed() removes, but one that reaches the far wall cuts a space
    as deep as the door is wide (a narrow balcony) in two."""
    return [(line.buffer(strip, cap_style="square"), outlines.DOOR_H_M) for pair in swings for line in pair]


def doorways(boxes: list, swings: list, rooms: list, strip: float) -> list:
    """The openings to take out of wall areas, once the rooms are known: the boxes, and of each swing the line that
    lies least inside a room (the closed leaf; the open one stands in the room). An opening drawn twice counts once."""
    inside = lambda line: max((line.intersection(p).length for p, _ in rooms), default=0.0)
    out = []
    for shape, h in boxes + swing_lines([(min(pair, key=inside),) for pair in swings], strip):
        if not any(shape.intersection(kept).area > 0.5 * shape.area for kept, _ in out):
            out.append((shape, h))
    return out


def _across(shape, by: float):
    """An opening widened across its wall by `by` each side, so it reaches the faces of the rooms either side: a
    window or a closed leaf is drawn inside the wall's thickness, the room outline runs along the wall's face."""
    from shapely.geometry import Polygon

    c = list(shape.minimum_rotated_rectangle.exterior.coords)[:4] if shape.minimum_rotated_rectangle.geom_type == "Polygon" else []
    if len(c) < 4:
        return shape.buffer(by)
    if math.dist(c[0], c[1]) < math.dist(c[1], c[2]):
        c = c[1:] + c[:1]  # c[0]-c[1] is the long side
    n = math.dist(c[1], c[2])
    ux, uy = (c[2][0] - c[1][0]) / n * by, (c[2][1] - c[1][1]) / n * by
    return Polygon([(c[0][0] - ux, c[0][1] - uy), (c[1][0] - ux, c[1][1] - uy), (c[2][0] + ux, c[2][1] + uy), (c[3][0] + ux, c[3][1] + uy)])


def enclosed(mass: list, openings: list, unit_m: float, ref: str = "dxf:walls", y_up: bool = True) -> list:
    """Tier 2: (outline, ref) for each room the wall mass encloses, top to bottom. Shared with vector_pdf.py."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    if not mass:
        raise NotImplementedError("the wall layers hold no geometry")
    shapes = [s for s, _ in openings]
    every = outlines.faces(mass, shapes, close=CLOSE_M / unit_m)  # with the slivers inside double-line walls
    found = [f for f in every if f.area >= MIN_AREA_M2 / unit_m ** 2 and not f.buffer(-MIN_WIDTH_M / unit_m / 2).is_empty]
    # A door line that ends in a room (the open leaf) stands there as a slit; closing the face by a few cm removes it.
    e = 1.5 * CLOSE_M / unit_m  # fills slits up to 6 cm; the lines are 4 cm thick
    found = [f.buffer(e, join_style="mitre").buffer(-e, join_style="mitre") for f in found]
    found = [max(getattr(f, "geoms", [f]), key=lambda g: g.area) for f in found if not f.is_empty]
    # The plan's footprint: the wall mass with every gap up to 3 m closed, filled. What rooms and walls leave
    # open inside it is a room that leaked through an opening nobody drew.
    solid = unary_union([*mass, *shapes])
    r = 1.5 / unit_m
    hull = solid.buffer(r, join_style="mitre").buffer(-r, join_style="mitre")
    footprint = unary_union([Polygon(g.exterior) for g in getattr(hull, "geoms", [hull]) if g.geom_type == "Polygon"])
    covered = (sum(f.area for f in every) + solid.intersection(footprint).area) / max(footprint.area, 1e-9)
    if covered < MIN_ENCLOSED:
        raise NotImplementedError(f"the walls enclose only {covered:.0%} of the plan: rooms are open to the outside")
    found.sort(key=lambda f: ((-1 if y_up else 1) * round(f.centroid.y / (r / 3)), f.centroid.x))
    return [(f, f"{ref}:{i + 1}") for i, f in enumerate(found)]


def read(doc, ignore=()):
    """(rooms as (outline, ref), openings as (shape, height m), metres per drawing unit or None, tier) from a DXF
    document, in drawing units. ignore: layer names to treat as absent (the check uses it to hide the room layer)."""
    from ezdxf import bbox

    msp = doc.modelspace()
    used = {e.dxf.layer for e in msp} - set(ignore)
    room_layers = {n for n in used if ROOM.search(n) and not LABELS.search(n)}
    kinds = {n: "wall" if WALL.search(n) else "door" if DOOR.search(n) else "window" if WINDOW.search(n) else None
             for n in used}
    kind_of = lambda layer: kinds.get(layer)
    if not room_layers and "wall" not in kinds.values():
        raise NotImplementedError("this DXF has no layer named as rooms or walls; its layers are "
                                  + ", ".join(sorted(l.dxf.name for l in doc.layers)[:12]))
    ext = bbox.extents(e for e in msp if e.dxf.layer in room_layers or kinds.get(e.dxf.layer))
    if not ext.has_data or not max(ext.size.x, ext.size.y):
        raise NotImplementedError("the room and wall layers of this DXF are empty")
    tol = max(ext.size.x, ext.size.y) / 20000
    known = M_PER_UNIT.get(doc.header.get("$INSUNITS"))
    # ponytail: without $INSUNITS only m, mm and cm are tried; a drawing in inches or feet gets no areas
    # (refused in tier 2, which needs sizes in metres to tell a room from a sliver)
    why = "no rooms found"
    for unit_m in [known] if known else [1.0, 0.001, 0.01]:
        try:
            strip = CLOSE_M / unit_m
            boxes, swings = _openings(msp, kind_of, tol, strip)
            tier = 1 if room_layers else 2
            rooms = _outlined(msp, room_layers, tol) if tier == 1 else \
                enclosed(_wall_mass(msp, kind_of, tol, THIN_M / unit_m), boxes + swing_lines(swings, strip), unit_m)
            openings = doorways(boxes, swings, rooms, strip)
        except NotImplementedError as e:
            why = str(e)
            continue
        areas = [p.area * unit_m ** 2 for p, _ in rooms]
        # One face inside a wall mass is a plan whose doorways were not closed, not a one-room home.
        if len(areas) >= tier and len(areas) <= 2000 and (
                PLAUSIBLE_M2[0] <= statistics.median(areas) <= PLAUSIBLE_M2[1] or known and statistics.median(areas) < 2000):
            return rooms, openings, unit_m, tier
        why = "the outlines found are not the size or number of rooms" if areas else why
    if room_layers and not known:  # outlines without a believable size are still rooms to count
        rooms = _outlined(msp, room_layers, tol)
        if rooms:
            return rooms, [], None, 1
    raise NotImplementedError(why)


def _texts(msp, ignore=()) -> list:
    out = []
    for e in msp.query("TEXT MTEXT"):
        if e.dxf.layer not in ignore:
            text = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).strip()
            if text:
                out.append((text, (e.dxf.insert.x, e.dxf.insert.y)))
    return out


def name_rooms(rooms: list, texts: list) -> list:
    """(label, number, names inside) per room from the texts standing in it, in drawing order: a text that reads as a
    number is a room number and the text just before it its label; with no number, the first text is the label.
    Printed areas are skipped. Several numbers in one outline are rooms no wall divides (an open plan): the labels
    are joined and the count is returned, so the reader can say so."""
    from shapely.geometry import Point

    inside: list[list[str]] = [[] for _ in rooms]
    for text, at in texts:
        hits = [i for i, (p, _) in enumerate(rooms) if p.contains(Point(at))]
        if hits and not AREA.fullmatch(text):
            inside[min(hits, key=lambda i: rooms[i][0].area)].append(text)
    out = []
    for words in inside:
        pairs, label = [], None
        for w in words:
            if NUMBER.fullmatch(w):
                pairs.append((label, w))
                label = None
            elif any(c.isalpha() for c in w):
                label = w
        if not pairs:
            first = next((w for w in words if any(c.isalpha() for c in w)), None)
            pairs = [(first, None)] if first else []
        pairs = list(dict.fromkeys(pairs))  # the same name printed twice is one name
        labels = list(dict.fromkeys(l for l, _ in pairs if l))
        out.append((" + ".join(labels) or None, pairs[0][1] if pairs else None, len(pairs)))
    return out


def build_scene(path: str, file: Path, ignore=()) -> Scene:
    import ezdxf

    from .swiss import _render
    try:
        doc = ezdxf.readfile(file)
    except (OSError, ezdxf.DXFError) as e:
        raise NotImplementedError(f"cannot read the DXF: {e}") from None
    rooms, openings, unit_m, tier = read(doc, ignore)
    to_page, page = _render(doc, path)
    entities = room_entities(rooms, name_rooms(rooms, _texts(doc.modelspace(), ignore)), to_page, unit_m, openings,
                             "room layer" if tier == 1 else "walls", 1.0 if tier == 1 else WALLS_CONF)
    return Scene(path=path, builder="dxf_rooms@1" if tier == 1 else "dxf_walls@1", tier="parse", covers=["room"],
                 pages=[page], entities=entities)


def room_entities(rooms: list, names: list, to_page, unit_m: float | None, openings: list, source: str, conf: float) -> list[Entity]:
    """Room entities from (outline, ref) and their names; shared with vector_pdf.py. A drawing that names its rooms
    (most outlines hold text) has no room without one: an outline with no text is then a shaft, a duct or a hole."""
    if 2 * sum(n > 0 for _, _, n in names) >= len(names):
        rooms, names = [r for r, nm in zip(rooms, names) if nm[2]], [nm for nm in names if nm[2]]
    if unit_m:
        openings = [(_across(shape, ACROSS_M / unit_m), h) for shape, h in openings]
    entities, ids = [], set()
    for i, ((poly, ref), (label, number, inside)) in enumerate(zip(rooms, names)):
        rid, k = f"room-{number or i + 1}", 1
        while rid in ids:
            k += 1
            rid = f"room-{number or i + 1}-{k}"
        ids.add(rid)
        room = Entity(id=rid, type="room", label=label, number=number, geom=[to_page(x, y) for x, y in poly.exterior.coords[:-1]],
                      qty={"m2": round(poly.area * unit_m ** 2, 2)} if unit_m else {}, conf=conf,
                      attrs={"outline_from": source} | ({"rooms_inside": inside} if inside > 1 else {}), ref=ref)
        if unit_m:
            outlines.measure(room, poly, unit_m, openings)
        entities.append(room)
    return entities


if __name__ == "__main__":
    import ezdxf

    assert DOOR.search("A-DOOR") and DOOR.search("Tueren") and not DOOR.search("FIXTURE") and WALL.search("BACK_WALL")
    assert ROOM.search("A-AREA") and ROOM.search("Raumpolygon") and not ROOM.search("UNIT_OUTLINE") and LABELS.search("ROOM_TEXT")

    def plan(units, k=1.0, room_layer=False, door="box"):
        """Two 4.7 x 4.6 m rooms as outlines.py draws them: an outer wall as two rings of lines, a partition as
        two closed wall pieces with a 0.9 m doorway, drawn in units of 1/k m."""
        doc = ezdxf.new()
        doc.header["$INSUNITS"] = units  # 0: not set (ezdxf.new() writes metres)
        msp = doc.modelspace()
        s = lambda pts: [(x * k, y * k) for x, y in pts]
        box = lambda x0, y0, x1, y1: s([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        for ring in (box(0, 0, 10, 5), box(0.2, 0.2, 9.8, 4.8)):  # a double-line outer wall
            for a, b in zip(ring, ring[1:] + ring[:1]):
                msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
        for piece in (box(4.9, 0.2, 5.1, 2.0), box(4.9, 2.9, 5.1, 4.8)):
            msp.add_lwpolyline(piece, close=True, dxfattribs={"layer": "A-WALL"})
        if door == "box":
            msp.add_lwpolyline(box(4.9, 2.0, 5.1, 2.9), close=True, dxfattribs={"layer": "A-DOOR"})
        elif door == "arc":  # hinge at (5, 2), closed towards (5, 2.9), open along the wall towards x = 5.9
            msp.add_arc((5 * k, 2 * k), 0.9 * k, 0, 90, dxfattribs={"layer": "A-DOOR"})
        if room_layer:
            msp.add_lwpolyline(box(0.2, 0.2, 4.9, 4.8), close=True, dxfattribs={"layer": "A-AREA"})
            msp.add_lwpolyline(box(5.1, 0.2, 9.8, 4.8), close=True, dxfattribs={"layer": "A-AREA"})
            msp.add_lwpolyline(box(0.1, 0.1, 9.9, 4.9), close=True, dxfattribs={"layer": "A-AREA"})  # the unit around them
        msp.add_text("Kitchen", dxfattribs={"layer": "A-TEXT", "insert": (2 * k, 3 * k)})
        msp.add_text("101", dxfattribs={"layer": "A-TEXT", "insert": (2 * k, 2.5 * k)})
        msp.add_text("21.6 m2", dxfattribs={"layer": "A-TEXT", "insert": (2 * k, 2 * k)})
        return doc

    areas = lambda found: [round(p.area * found[2] ** 2, 1) for p, _ in found[0]]
    got = read(plan(6))  # walls only, metres
    assert got[3] == 2 and areas(got) == [21.6, 21.6], areas(got)
    assert name_rooms(got[0], _texts(plan(6).modelspace())) == [("Kitchen", "101", 1), (None, None, 0)]
    assert name_rooms(got[0][:1], [("Whg. A", (2, 3)), ("Wohnen", (2, 3)), ("1.01", (2, 3)), ("Küche", (3, 3)), ("1.02", (3, 3))]) == [("Wohnen + Küche", "1.01", 2)]
    s = outlines.surface(got[0][0][0], got[2], openings=got[1])
    assert (round(s["perimeter_m"], 1), s["openings"], round(s["net_m2"], 1)) == (18.6, 1, 44.7), s
    got = read(plan(0, 1000))  # the same in millimetres with no units set
    assert got[2] == 0.001 and areas(got) == [21.6, 21.6], (got[2], areas(got))
    got = read(plan(6, door="arc"))  # a door drawn as its swing only: the hinge-to-arc line closes the doorway
    assert len(got[0]) == 2 and all(abs(a - 21.6) < 0.2 for a in areas(got)), areas(got)
    assert abs(got[0][0][0].length - 18.6) < 0.5, got[0][0][0].length  # no slit left by the open leaf
    try:
        read(plan(6, door=None))  # the open doorway joins the rooms into one face, which is not a plan to trust
        raise AssertionError("one enclosed face was accepted as a plan")
    except NotImplementedError:
        pass
    got = read(plan(6, room_layer=True))  # room outlines win, and the unit drawn around them is not a room
    assert got[3] == 1 and areas(got) == [21.6, 21.6], areas(got)
    try:
        read(ezdxf.new())
        raise AssertionError("an empty DXF was read")
    except NotImplementedError:
        pass

    import os
    root = Path(os.environ.get("BLUEPRINTS_DATA", "junction-dataset")) / "data"
    file = root / "swiss_dwellings" / "site3387_bldg5238_floor23498.dxf"
    if not file.exists():
        print("synthetic checks ok; skipped the dataset check")
        raise SystemExit
    by_layer = build_scene("uploads/check.dxf", file)  # tier 1 on ROOM_OUTLINE: the Swiss reader's areas
    rooms = {e.number: e for e in by_layer.entities}
    assert by_layer.builder == "dxf_rooms@1" and len(rooms) == 24 and (rooms["7.01"].qty["m2"], rooms["7.16"].qty["m2"]) == (15.47, 5.63)
    assert rooms["7.01"].label and rooms["7.01"].qty["m"] > 10 and rooms["7.01"].qty["wall_m2"] > 20
    by_walls = build_scene("uploads/check.dxf", file, ignore={"ROOM_OUTLINE", "ROOM_TEXT", "UNIT_OUTLINE"})
    # 24 numbered rooms; the walls enclose 19 spaces, because open-plan rooms have no wall between them
    assert by_walls.builder == "dxf_walls@1" and len(by_walls.entities) == 19, len(by_walls.entities)
    assert abs(sum(e.qty["m2"] for e in by_walls.entities) - sum(e.qty["m2"] for e in by_layer.entities)) < 3
    for name in ("opening_design/Crema_Cafe/Models/crema cafe 6.blend.dxf", "opening_design/Lake_Kegonsa_Home/Model/northstar/northstar.dxf"):
        try:
            build_scene("uploads/x.dxf", root / name)
            raise AssertionError(f"{name} was read as a plan")
        except NotImplementedError:
            pass
    print("ok")
