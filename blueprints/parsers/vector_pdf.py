"""Lane 3. A vector PDF floor plan that is not in the Swiss renderer's style -> Scene with room outlines and areas.

Two ways to find rooms on page 1, by how the page is filled:
  1  rooms drawn as tinted fills, one per room, holding the room texts (the plans rendered from IFC models).
  2  walls drawn as dark fills: the wall mass, with every box that bridges two wall ends (a door frame, a window)
     and every door swing painted in as wall, encloses the rooms (outlines.faces). Refused when the walls leave
     more than a tenth of the plan open, so a leaking room is rarely dropped in silence; it refuses often.
Labels are the texts standing inside a room. Scale: "1:100" text, else calibrated from printed room areas, else
rooms without areas. Walls drawn as lines, sheets of several plans, scans and anything else raise
NotImplementedError, so the upload goes to the raster reader.

Covers rooms only: openings are used to close the rooms and for the wall area, not counted.

Check:  uv run --extra parsers python -m blueprints.parsers.vector_pdf
"""
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .. import outlines
from ..schemas import Scene
from .dxf import ACROSS_M, CLOSE_M, doorways, enclosed, name_rooms, room_entities, swing_lines
from .pdf import M_PER_PT, PRINTED, SCALE, _subpaths

MAX_PATHS = 20000  # a sheet of details, hatches or outlined lettering: not a plan this reader can take apart
TINTS_HOLD = 0.7  # tier 1: the tinted fills must hold this share of the texts standing among them
# Tier 2 rooms: on the 22 of 56 plans rendered from IFC models that it accepted, 86% of the rooms came out as their
# tinted fill (IoU >= 0.8), with a median area error of 0.5%; two accepted plans still missed a fifth of their area.
PDF_WALLS_CONF = 0.86


def _fills(drawings) -> dict:
    """Fill colour (rounded) -> [(polygon, name of its path)], for every filled path. A path of several rings is
    filled even-odd: a ring inside another is a hole, rings apart are pieces of their own (a wall drawn in one path
    as the segments between its windows)."""
    from shapely.geometry import Polygon

    out = defaultdict(list)
    for i, d in enumerate(drawings):
        if not d.get("fill") or d["type"] not in ("f", "fs"):
            continue
        shape = Polygon()
        for ring in sorted((Polygon(s).buffer(0) for s in _subpaths(d) if len(s) >= 3), key=lambda r: -r.area):
            shape = shape.symmetric_difference(ring)
        parts = [g for g in getattr(shape, "geoms", [shape]) if g.geom_type == "Polygon" and g.area > 0]
        out[tuple(round(c, 2) for c in d["fill"])] += [(g, f"{i}.{j}" if j else str(i)) for j, g in enumerate(parts)]
    return out


def _tinted(fills: dict, texts: list):
    """Tier 1: the fills of one pale colour that hold the page's room texts, or None."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    best = None
    for colour, drawn in fills.items():
        polys = []  # a room drawn twice (a model holding the same space again) is one room
        for p, i in drawn:
            if not any(p.intersection(q).area > 0.7 * p.union(q).area for q, _ in polys):
                polys.append((p, i))
        if min(colour) < 0.75 or min(colour) > 0.99 or len(polys) < 2:
            continue
        region = unary_union([p for p, _ in polys]).envelope
        among = [Point(at) for _, at in texts if region.contains(Point(at))]
        held = sum(any(p.contains(pt) for p, _ in polys) for pt in among)
        named = sum(any(p.contains(pt) for pt in among) for p, _ in polys)
        if among and held >= TINTS_HOLD * len(among) and 2 * named >= len(polys) and (best is None or named > best[0]):
            best = (named, polys)
    return best and [(p, f"pdf:path{i}") for p, i in best[1]]


def _walls(fills: dict) -> list:
    """The wall mass: the dark fill colour covering most area, plus dark fills of other colours that touch it
    (columns, a second wall type)."""
    from shapely.ops import unary_union

    dark = {c: [p for p, _ in polys] for c, polys in fills.items() if sum(c) / len(c) < 0.75}
    if not dark:
        return []
    main = max(dark, key=lambda c: sum(p.area for p in dark[c]))
    mass = unary_union(dark[main]).buffer(0.5)
    return dark[main] + [p for c, polys in dark.items() if c != main for p in polys if p.intersects(mass)]


def _bridges(drawings, mass: list, m_per_pt: float) -> list:
    """Boxes the size of an opening (up to 0.7 x 4 m) that stand in a gap of the wall mass, with a wall end at both
    short sides and nothing along the long ones: door frames and windows. A fixture against a wall and a layer
    drawn inside a wall are left alone."""
    import math

    from shapely.geometry import MultiPoint, Polygon
    from shapely.ops import unary_union

    walls, out, seen = unary_union(mass), [], set()
    for d in drawings:
        pts = tuple(sorted({p for s in _subpaths(d) for p in s}))
        if len(pts) < 4 or pts in seen:
            continue
        seen.add(pts)
        hull = MultiPoint(pts).convex_hull
        box = hull.minimum_rotated_rectangle
        if box.geom_type != "Polygon" or hull.area < 0.95 * box.area or walls.contains(box):
            continue  # not a rectangle (a window may be several nested ones), or a piece of wall
        c = list(box.exterior.coords)[:4]
        if math.dist(c[0], c[1]) < math.dist(c[1], c[2]):
            c = c[1:] + c[:1]  # c[0]-c[1] is the long side
        long, short = math.dist(c[0], c[1]), math.dist(c[1], c[2])
        if not short or short * m_per_pt > 0.7 or long * m_per_pt > 4:
            continue
        lx, ly = (c[1][0] - c[0][0]) / long * 0.5, (c[1][1] - c[0][1]) / long * 0.5  # half a point along, and across
        sx, sy = (c[2][0] - c[1][0]) / short * 0.5, (c[2][1] - c[1][1]) / short * 0.5
        band = lambda a, b, dx, dy: Polygon([a, b, (b[0] + dx, b[1] + dy), (a[0] + dx, a[1] + dy)])
        ends = [band(c[3], c[0], -lx, -ly), band(c[1], c[2], lx, ly)]
        sides = [band(c[0], c[1], -sx, -sy), band(c[2], c[3], sx, sy)]
        # a wall end at both short sides, free along the long ones (a layer inside a wall has wall along it)
        if all(walls.intersection(e).area > 0.3 * e.area for e in ends) and \
                sum(walls.intersection(s).area for s in sides) < 0.3 * sum(s.area for s in sides):
            out.append(box)
    return out


def _swings(drawings, m_per_pt: float) -> list:
    """Door swings: a stroked path whose points all lie at one door-sized distance from its first or last point
    (the hinge; the rest is the leaf end and the arc). Each gives the two lines from the hinge to the ends of the
    arc, as dxf.py does for ARCs: one is the closed leaf across the doorway."""
    import math

    from shapely.geometry import LineString

    out = []
    for d in drawings:
        for pts in _subpaths(d) if d["type"] == "s" else ():
            for hinge, rest in ((pts[0], pts[1:]), (pts[-1], pts[:-1])):
                radii = [math.dist(hinge, p) for p in rest]
                if len(rest) >= 2 and 0.5 <= statistics.median(radii) * m_per_pt <= 1.6 and max(radii) - min(radii) <= 0.03 * max(radii) \
                        and math.dist(rest[0], rest[-1]) > 0.5 * max(radii):
                    out.append((LineString([hinge, rest[0]]), LineString([hinge, rest[-1]])))
                    break
    return out


def read(page, tiers=(1, 2)):
    """(rooms as (outline, ref), openings as (shape, height m), metres per point or None, tier, texts) for a page,
    in PDF points. tiers: which ways to try (the check uses it to hide the room fills)."""
    from shapely.geometry import Point

    if page.rotation:
        page.remove_rotation()
    drawings = page.get_drawings()
    if not drawings:
        raise NotImplementedError("the page has no vector drawing: a scan or an image")
    if len(drawings) > MAX_PATHS:
        raise NotImplementedError(f"the page has {len(drawings)} paths: too dense to take apart as one plan")
    texts = [(s["text"].strip(), ((s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2))
             for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"] if s["text"].strip()]
    scales = Counter(int(m[1]) for text, _ in texts for m in SCALE.finditer(text) if 20 <= int(m[1]) <= 1000)
    m_per_pt = scales.most_common(1)[0][0] * M_PER_PT if scales else None
    fills = _fills(drawings)

    # ponytail: sizes are judged at 1:100 when the page states no scale; a plan at 1:50 or 1:200 without a scale
    # note may lose small rooms or keep slivers
    size = m_per_pt or 100 * M_PER_PT
    mass = _walls(fills)
    boxes = _bridges(drawings, mass, size) if mass else []
    swings, strip = _swings(drawings, size), CLOSE_M / size
    rooms, tier = (_tinted(fills, texts) if 1 in tiers else None), 1
    if not rooms:
        if 2 not in tiers:
            raise NotImplementedError("no tinted room fills holding the room texts")
        if not mass:
            raise NotImplementedError("no room fills and no filled walls: the walls of this PDF are drawn as lines")
        rooms, tier = enclosed(mass, [(b, 0) for b in boxes] + swing_lines(swings, strip), size, "pdf:walls", y_up=False), 2
    # a box between two rooms is a door, one on a single room a window (an entrance door counts as a window)
    reach = ACROSS_M / size
    boxes = [(b, outlines.DOOR_H_M if sum(p.intersects(b.buffer(reach)) for p, _ in rooms) > 1 else outlines.WINDOW_H_M)
             for b in boxes]
    openings = doorways(boxes, swings, rooms, strip)
    if not m_per_pt:  # no scale note: calibrate on the areas printed in the rooms, as pdf.py does
        fits = []
        for text, at in texts:
            hit = [p for p, _ in rooms if p.contains(Point(at))] if PRINTED.fullmatch(text) else []
            if hit:
                fits.append((float(PRINTED.fullmatch(text)[1].replace(",", ".")) / min(hit, key=lambda p: p.area).area) ** 0.5)
        m_per_pt = statistics.median(fits) if fits else None
    areas = [p.area * (m_per_pt or size) ** 2 for p, _ in rooms]
    # One face inside a wall mass is a plan whose doorways were not closed, not a one-room home.
    if len(areas) < tier or len(areas) > 500 or not 1 <= statistics.median(areas) <= 500:
        raise NotImplementedError(f"the {len(areas)} outlines found are not the size or number of rooms")
    return rooms, openings, m_per_pt, tier, texts


def build_scene(path: str, file: Path) -> Scene:
    import pymupdf

    from .pdf import _render
    try:
        doc = pymupdf.open(file, filetype="pdf")
        page = doc[0]
    except Exception as e:  # a damaged or empty file
        raise NotImplementedError(f"cannot open the PDF: {e}") from None
    rooms, openings, m_per_pt, tier, texts = read(page)
    W, H = page.rect.width, page.rect.height
    to_page = lambda x, y: (min(max(x / W, 0), 1), min(max(y / H, 0), 1))
    entities = room_entities(rooms, name_rooms(rooms, texts), to_page, m_per_pt, openings,
                             "room fills" if tier == 1 else "walls", 1.0 if tier == 1 else PDF_WALLS_CONF)
    return Scene(path=path, builder="pdf_rooms@1" if tier == 1 else "pdf_walls@1", tier="parse", covers=["room"],
                 pages=[_render(page, path)], entities=entities)


if __name__ == "__main__":
    import pymupdf

    keep = []

    def plan(tint: bool, scale: str | None = "Scale 1:100"):
        """Two 4.7 x 4.6 m rooms at 1:100 (as outlines.py and dxf.py draw them): dark wall fills, a door frame box
        in the partition, a fixture box against a wall, and room texts."""
        k = 1 / (100 * M_PER_PT)  # points per metre on paper
        doc = pymupdf.open()
        keep.append(doc)  # a page dies with its document
        page = doc.new_page(width=14 * k, height=9 * k)
        r = lambda x0, y0, x1, y1: pymupdf.Rect((x0 + 2) * k, (y0 + 2) * k, (x1 + 2) * k, (y1 + 2) * k)
        for wall in (r(0, 0, 10, 0.2), r(0, 4.8, 10, 5), r(0, 0, 0.2, 5), r(9.8, 0, 10, 5), r(4.9, 0.2, 5.1, 2.0), r(4.9, 2.9, 5.1, 4.8)):
            page.draw_rect(wall, color=None, fill=(0.3, 0.3, 0.3))
        if tint:
            page.draw_rect(r(0.2, 0.2, 4.9, 4.8), color=None, fill=(0.89, 0.93, 0.97))
            page.draw_rect(r(5.1, 0.2, 9.8, 4.8), color=None, fill=(0.89, 0.93, 0.97))
        page.draw_rect(r(4.9, 2.0, 5.1, 2.9), color=(0.3, 0.3, 0.3), fill=None)  # door frame
        page.draw_rect(r(0.2, 1, 0.8, 1.5), color=(0, 0, 0), fill=(1, 1, 1))  # a basin on the wall: not an opening
        for text, x, y in (("Kitchen", 1.5, 2), ("101", 1.5, 2.4), ("21.62 m²", 1.5, 2.8), ("Bedroom", 6.5, 2), ("102", 6.5, 2.4)):
            page.insert_text(((x + 2) * k, (y + 2) * k), text, fontsize=8)
        if scale:
            page.insert_text((2 * k, 8 * k), scale, fontsize=8)
        return page

    for tint in (True, False):
        rooms, openings, m_per_pt, tier, texts = read(plan(tint))
        assert tier == (1 if tint else 2) and [round(p.area * m_per_pt ** 2, 1) for p, _ in rooms] == [21.6, 21.6], (tier, rooms)
        assert sorted(name_rooms(rooms, texts)) == [("Bedroom", "102", 1), ("Kitchen", "101", 1)]
    assert len(openings) == 1 and openings[0][1] == outlines.DOOR_H_M  # the frame, not the basin
    s = outlines.surface(rooms[0][0], m_per_pt, openings=openings)
    assert (round(s["perimeter_m"], 1), s["openings"], round(s["net_m2"], 1)) == (18.6, 1, 44.7), s
    rooms, _, m_per_pt, *_ = read(plan(True, scale=None))  # no scale note: calibrated on the printed 21.62 m²
    assert abs(m_per_pt / (100 * M_PER_PT) - 1) < 0.01, m_per_pt
    try:
        keep.append(pymupdf.open())
        read(keep[-1].new_page())
        raise AssertionError("an empty page was read")
    except NotImplementedError:
        pass

    import os
    root = Path(os.environ.get("BLUEPRINTS_DATA", "junction-dataset")) / "data"
    file = root / "ifc_models" / "kit-institute_ac20-institute-var-2_plan-01-keller.pdf"
    if not file.exists():
        print("synthetic checks ok; skipped the dataset check")
        raise SystemExit
    scene = build_scene("uploads/check.pdf", file)  # tinted room fills, "Maßstab 1:100"
    rooms = {e.number: e for e in scene.entities}
    assert scene.builder == "pdf_rooms@1" and len(rooms) == 17 and rooms["006"].label == "Seminarraum", sorted(rooms)
    assert rooms["006"].qty["m2"] > 50 and rooms["006"].qty["m"] > 20 and rooms["006"].qty["wall_m2"] > 50, rooms["006"].qty
    try:
        build_scene("uploads/x.pdf", root / "opening_design/Crema_Cafe/Models/pdfs/A-100 - Floor Plans v3.pdf")
        raise AssertionError("the Crema Cafe sheet was read as a plan")
    except NotImplementedError:
        pass
    print("ok")
