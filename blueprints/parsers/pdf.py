"""Lane A. An uploaded vector PDF floor plan on its own (no DXF beside it) -> Scene.

Reads the Swiss Dwellings renderer's style: the PDF is the DXF drawn at 1:100 or 1:200, each DXF layer in its own
stroke and fill, so a path's style and shape say what it is (checked against the DXF on the train split):
  room       closed path, stroke grey 0.54, fill white / 0.894 (outdoor) / 0.953 (shared) / 0.812;
             a room with a hole is one path with an extra subpath, or an outline lying inside another
  apartment  dashed coloured stroke that closes on itself (the "Wohnungsgrenze"), marked by a "Whg. A" text inside
  door       a stroke-only quad in grey 0.27 (the door box; swing arcs and leaves are drawn separately)
  window     a white box with a black stroke, standing in a wall gap (its centre in no room)
  fixture    a white box with a black stroke inside a room, named by the symbol drawn in it (as swiss_fixtures.py)
With a scale, sizes are measured on the paths as swiss.py measures them on the DXF (README.md, "Sizes").
Room labels, numbers and apartment markers are real text, in drawing order: [marker] label number [area].
Anything else raises NotImplementedError, so the upload falls back to the vision reader.

Check:  uv run python -m blueprints.parsers.pdf
"""
import hashlib
import re
from collections import Counter
from pathlib import Path

from ..outlines import DOOR_H_M, WINDOW_H_M, measure
from ..schemas import Entity, Page, Scene
from .swiss import (AREA, MARKER, NUMBER, OUTDOOR, OUTDOOR_GREY, ROOM_EDGE, SHARED_GREY, WALL_EDGE, label_units,
                    located, room_dims, sized, unit_index)

M_PER_PT = 0.0254 / 72  # one point on paper, in metres, at 1:1
SCALE = re.compile(r"\b1\s*:\s*(\d{2,4})\b")  # "Massstab 1:100 (A3)", "Echelle 1:200"
PRINTED = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2)")  # "15.61 m²", next to a room number
# @2: perimeters, room and opening sizes; @3: net wall area. pipeline.get_scene rebuilds older cached scenes
BUILDER = "pdf_vector@3"


def _grey(rgb) -> float | None:
    return round(rgb[0], 3) if rgb and max(rgb) - min(rgb) < 0.01 else None


def _subpaths(d) -> list[list[tuple[float, float]]]:
    """A path's corner points, split where an item does not start at the previous item's end."""
    out, last = [], None
    for it in d["items"]:
        if it[0] == "re":
            r = it[1]
            out.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
            last = None
            continue
        pts = (it[1].ul, it[1].ur, it[1].lr, it[1].ll) if it[0] == "qu" else (it[1], it[-1])
        pts = [(p.x, p.y) for p in pts]
        if last is None or abs(pts[0][0] - last[0]) + abs(pts[0][1] - last[1]) > 0.01:
            out.append([pts[0]])
        out[-1] += pts[1:]
        last = pts[-1]
    return [[p for i, p in enumerate(s) if i == 0 or p != s[i - 1]] for s in out]


def _polygon(subpaths):
    """Largest ring minus the rings inside it; a self-crossing ring keeps its largest piece."""
    from shapely.geometry import Polygon
    rings = sorted((Polygon(s).buffer(0) for s in subpaths if len(s) >= 3), key=lambda p: -p.area)
    if not rings:
        return None
    poly = rings[0]
    for r in rings[1:]:
        if r.within(poly):
            poly = poly.difference(r)
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly if poly.geom_type == "Polygon" and poly.area > 0 else None


def _render(page, path: str) -> Page:
    """Page 1 as pages/<md5(path)>.png, long side 2048 px, as vision._page renders uploads."""
    import pymupdf

    from .. import pipeline
    scale = 2048 / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
    image = f"pages/{hashlib.md5(path.encode()).hexdigest()}.png"
    (pipeline.CACHE / "pages").mkdir(parents=True, exist_ok=True)
    (pipeline.CACHE / image).write_bytes(pix.tobytes("png"))
    return Page(image=image, width=pix.width, height=pix.height)


def _symbol(kinds: list[str]) -> str | None:
    """Fixture subtype from the primitives inside its box, the rules of swiss_fixtures.py: P<vertices> or C."""
    if "P69" in kinds:
        return "bathtub"
    if "P48" in kinds:
        return "toilet" if "P2" in kinds else "sink"
    if "P5" in kinds and "C" in kinds:
        return "shower"
    return None


def build_scene(path: str, file: Path) -> Scene:
    import pymupdf
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union

    try:
        doc = pymupdf.open(file, filetype="pdf")
    except Exception as e:  # a damaged file: no parser here can read it either
        raise NotImplementedError(f"cannot open the PDF: {e}") from None
    if doc.page_count < 1:
        raise NotImplementedError("the PDF has no pages")
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    drawings = page.get_drawings()

    outlines, units, door_boxes, white_boxes, parts = [], [], [], [], []
    for d in drawings:
        stroke, fill, items = _grey(d.get("color")), _grey(d.get("fill")), d["items"]
        if d["type"] == "fs" and stroke is not None and 0.5 < stroke < 0.6 and fill is not None and fill > 0.8:
            if poly := _polygon(_subpaths(d)):
                outlines.append((poly, fill))
        elif d["type"] == "fs" and stroke == 0 and fill == 1 and len(_subpaths(d)) == 1 and len(_subpaths(d)[0]) in (4, 5):
            white_boxes.append([Polygon(_subpaths(d)[0][:4]), d["rect"], []])
        elif d["type"] == "s" and d.get("dashes") not in (None, "[] 0") and stroke is None and d.get("color"):
            pts = [p for s in _subpaths(d) for p in s]
            if len(pts) >= 4 and (Point(pts[0]).distance(Point(pts[-1])) < 1 or items[0][0] in ("qu", "re")):
                if unit := _polygon([pts]):
                    units.append(unit)
        elif d["type"] == "s" and len(items) == 1 and items[0][0] == "qu" and stroke and stroke < 0.5:
            door_boxes.append(_subpaths(d)[0][:4])  # grey; a shower tray is a black quad
        if d["type"] == "s" and d["width"] < 0.6:  # fixture symbol primitives, as swiss_fixtures.py names them; door leaves are 0.85
            kinds = [it[0] for it in items]
            kind = "C" if kinds == ["c"] * 8 else "P5" if kinds == ["qu"] else f"P{len(kinds) + 1}" if set(kinds) == {"l"} else None
            if kind:
                parts.append((d["rect"], kind, LineString([tuple(items[0][1]), tuple(items[0][2])]) if kind == "P2" else None))

    # Text-show operations in drawing order: get_text() merges the overlapping numbers of tiny rooms. One word can
    # take several operations ("Büro", "fl", "äche" by font subset); those abut on one baseline, so join them.
    spans = []
    for t in page.get_texttrace():
        text, bb = "".join(chr(c[0]) for c in t["chars"]), t["bbox"]
        if spans and abs(spans[-1][1][1] - bb[1]) < 0.5 and abs(bb[0] - spans[-1][1][2]) < 0.5:
            prev, pb = spans.pop()
            text, bb = prev + text, (pb[0], min(pb[1], bb[1]), bb[2], max(pb[3], bb[3]))
        spans.append((text, bb))
    # The plan is where the rooms are; the legend and title block below it repeat every symbol once.
    if not outlines:
        raise NotImplementedError("no room outlines in the style this PDF reader knows")
    plan = unary_union([p for p, _ in outlines]).envelope.buffer(0.03 * max(W, H))
    inplan = lambda r: plan.contains(Point((r[0] + r[2]) / 2, (r[1] + r[3]) / 2))

    # Room texts in drawing order: [marker] label number [area]. A number closes a room.
    rooms, pending, markers, printed = [], [], [], {}
    for text, bb in spans:
        text = text.strip()
        if not text or not inplan(bb):
            continue
        at = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
        if NUMBER.fullmatch(text):
            rooms.append((pending[-1] if pending else None, text, at))
            if markers and markers[-1][2] is None:
                markers[-1][2] = at
            pending = []
        elif m := MARKER.fullmatch(text):
            markers.append([m[1], at, None])
        elif m := PRINTED.fullmatch(text):
            if rooms:
                printed[len(rooms) - 1] = float(m[1].replace(",", "."))
        elif not AREA.fullmatch(text):
            pending.append(text)
    if not rooms:
        raise NotImplementedError("no numbered room labels inside room outlines")

    # Scale: the title block's "1:100"; else calibrated from printed room areas; else no areas at all.
    scales = Counter(int(m[1]) for text, _ in spans for m in SCALE.finditer(text) if 20 <= int(m[1]) <= 1000)
    rooms_at = [min((p for p, _ in outlines if p.contains(Point(at))), key=lambda p: p.area, default=None)
                for _, _, at in rooms]
    hollow = lambda poly: poly.difference(unary_union([p for p, _ in outlines if p is not poly and p.within(poly)]))
    net = lambda poly: hollow(poly).area
    if scales:
        scale = scales.most_common(1)[0][0]
    else:
        fits = sorted((a / net(rooms_at[i])) ** 0.5 / M_PER_PT for i, a in printed.items() if rooms_at[i])
        scale = fits[len(fits) // 2] if fits else None
    m_per_pt = scale * M_PER_PT if scale else None

    shape = lambda poly: [(min(max(x / W, 0), 1), min(max(y / H, 0), 1)) for x, y in poly.exterior.coords[:-1]]
    unit_label = label_units(markers, units, 1 / m_per_pt if m_per_pt else 30)
    entities = [Entity(id=f"apt-{letter}", type="apartment", label=letter, geom=shape(units[i]), ref=f"pdf:unit{i}")
                for i, letter in unit_label.items()]
    fill_of = {id(p): f for p, f in outlines}
    ids, placed = set(), []
    for (label, num, at), poly in zip(rooms, rooms_at):
        rid, k = f"room-{num}", 1
        while rid in ids:  # duplicate numbers on one floor
            k += 1
            rid = f"room-{num}-{k}"
        ids.add(rid)
        fill = fill_of[id(poly)] if poly else None
        entities.append(room := Entity(
            id=rid, type="room", label=label, number=num,
            apartment=None if fill == SHARED_GREY else unit_label.get(unit_index(units, *at)),
            outdoor=(label or "").casefold() in OUTDOOR or fill == OUTDOOR_GREY,
            geom=shape(poly) if poly else [], qty={"m2": round(net(poly) * m_per_pt ** 2, 2)} if poly and m_per_pt else {},
            ref=f"pdf:text{num}"))
        if poly:
            placed.append((poly, room))
            if m_per_pt:
                room.qty["m"], room.attrs["width_m"], room.attrs["length_m"] = room_dims(poly, m_per_pt)

    # Each symbol primitive belongs to the smallest white box around it; plain lines to every box they lie in
    # (tested against the box itself: a rotated box's bounding rectangle catches door leaves beside it).
    inside = lambda a, b: b.x0 - 0.5 <= a.x0 and b.y0 - 0.5 <= a.y0 and a.x1 <= b.x1 + 0.5 and a.y1 <= b.y1 + 0.5
    white_boxes = [b for b in white_boxes if inplan(tuple(b[1]))]
    for r, kind, line in parts:
        owners = [b for b in white_boxes if (b[0].buffer(0.5).contains(line) if line else inside(r, b[1]))]
        if owners and kind != "P2":
            owners = [min(owners, key=lambda b: b[1].get_area())]
        for b in owners:
            b[2].append(kind)
    in_room = lambda p: any(poly.contains(p) for poly, _ in outlines)
    near = 0.05 / m_per_pt if m_per_pt else 1.4

    found: dict[tuple, list] = {}  # (kind, rounded corners) -> [kind, pts, copies]: exact copies count once
    for box, r, kinds in white_boxes:
        sub = _symbol(kinds)
        kind = sub or ("window" if "P2" in kinds and not in_room(box.centroid) else None)
        if kind:
            pts = list(box.exterior.coords[:-1])
            found.setdefault((kind, *(round(v, 2) for p in pts for v in p)), [kind, pts, 0])[2] += 1
    for pts in door_boxes:
        if inplan(Polygon(pts).bounds):
            found.setdefault(("door", *(round(v, 2) for p in pts for v in p)), ["door", pts, 0])[2] += 1

    # Door and window widths need the scale. The PDF draws each wall as one outline running across its openings, so
    # only the room outlines tell an opening's width from the wall's thickness (swiss.py's tolerance, in points).
    # In a thin partition that reach also catches the jambs, so a box it cannot place is tried once more at 5 mm.
    room_edges = unary_union([p.boundary for p, _ in outlines])
    zones = [(room_edges.buffer(t / m_per_pt), False) for t in (ROOM_EDGE, WALL_EDGE)] if m_per_pt else []
    if m_per_pt:  # each room's wall area net of the doors and windows on its outline (qty wall_m2)
        cuts = [(Polygon(pts), DOOR_H_M if kind == "door" else WINDOW_H_M) for kind, pts, _ in found.values() if kind in ("door", "window")]
        for poly, room in placed:
            measure(room, hollow(poly), m_per_pt, cuts)

    n = Counter()
    for kind, pts, copies in found.values():
        n[kind] += 1
        fixture = kind not in ("door", "window")
        where = located("fixture" if fixture else kind, pts, copies, placed, near)
        dims = sized(pts, zones, m_per_pt) if m_per_pt and not fixture else {}
        where["attrs"] |= dims.get("more", {})
        entities.append(Entity(
            id=f"fixture-{kind}-{n[kind]}" if fixture else f"{kind}-{n[kind]}", type="fixture" if fixture else kind,
            subtype=kind if fixture else None, geom=[(min(max(x / W, 0), 1), min(max(y / H, 0), 1)) for x, y in pts],
            ref=f"pdf:{kind}{n[kind]}", qty=dims.get("qty", {}), **where))

    # Found the renderer's rooms and their texts, so its doors, windows and fixtures are drawn its way too: none
    # drawn means none. Areas are left out without a scale, so area questions are refused rather than guessed.
    covers = ["room", "apartment", "door", "window", "fixture:toilet", "fixture:sink", "fixture:bathtub", "fixture:shower"]
    return Scene(path=path, builder=BUILDER, tier="parse", covers=covers, pages=[_render(page, path)], entities=entities)


if __name__ == "__main__":
    import os

    root = Path(os.environ.get("BLUEPRINTS_DATA", "junction-dataset")) / "data" / "swiss_dwellings"
    s = build_scene("uploads/check.pdf", root / "site3387_bldg5238_floor23498.pdf")
    rooms = {e.number: e for e in s.entities if e.type == "room"}
    count = lambda t, sub=None: sum(e.type == t and e.subtype == sub for e in s.entities)
    assert len(rooms) == 24 and (rooms["7.01"].qty["m2"], rooms["7.16"].qty["m2"]) == (15.47, 5.63), rooms["7.01"]
    assert rooms["7.05"].qty == {"m2": 6.01, "m": 11.57, "wall_m2": 27.17}  # perimeter and net wall area, as the DXF reader
    assert sorted(e.label for e in s.entities if e.type == "apartment") == ["A", "B"]
    assert (rooms["7.03"].apartment, rooms["7.24"].apartment, rooms["7.01"].outdoor) == ("A", None, True)
    assert (count("door"), count("window")) == (18, 11)  # the DXF's 20 door boxes hold 2 exact copies
    assert [count("fixture", k) for k in ("toilet", "sink", "bathtub", "shower")] == [4, 4, 2, 1]
    room = lambda p, n: next(e for e in build_scene("uploads/c.pdf", root / f"{p}.pdf").entities if e.number == n)
    assert room("site2155_bldg3005_floor13704", "4.11").qty["m2"] == 14.7  # outline minus the hole inside it
    # Sizes agree with the DXF's to the PDF's coordinate precision (2 cm), and every door and window gets one.
    bed = rooms["7.08"]  # a plain rectangle; the DXF gives 15.926, 3.545, 4.418
    assert max(abs(a - b) for a, b in zip((bed.qty["m"], bed.attrs["width_m"], bed.attrs["length_m"]), (15.926, 3.545, 4.418))) <= 0.02
    widths = sorted(e.qty["m"] for e in s.entities if e.type in ("door", "window"))
    assert len(widths) == 29 and 0.5 <= widths[0] and widths[-1] <= 3.5, widths
    assert abs(sorted(e.qty["m"] for e in s.entities if e.type == "door")[9] - 0.878) <= 0.02  # the plan's usual door
    assert room("site2135_bldg2978_floor13581", "2.01").outdoor  # unlabelled balcony, known by its grey fill

    # No scale text: calibrate from printed areas ("15.61 m²"); with neither, no areas, so area questions are refused.
    from ..executor import execute
    from ..schemas import Query, QueryPlan
    SCALE = re.compile(r"$^")
    assert room("site1000_bldg2082_floor9385", "2.05").qty["m2"] == 18.36  # the DXF value; 2.01 prints 15.61
    PRINTED = SCALE
    s = build_scene("uploads/c.pdf", root / "site3387_bldg5238_floor23498.pdf")
    plan = QueryPlan(queries={"a": Query(op="sum", type="room", number="7.01", qty="m2")}, unit="m2")
    assert execute(plan, s).answer is None and len([e for e in s.entities if e.type == "room"]) == 24
    assert not any(e.qty for e in s.entities), "no scale, so nothing in metres"
    print("ok")
