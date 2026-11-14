"""Lane A. Swiss Dwellings floor plan -> Scene.

Questions name the .pdf. The same-name .dxf (file.with_suffix(".dxf")) has one layer per kind of thing:
DOOR, WINDOW, FIXTURE, ROOM_OUTLINE, ROOM_TEXT, UNIT_OUTLINE, WALL. The same-name .png is the page image.

Rooms, apartments, doors, windows and sanitary fixtures (toilets, sinks, bathtubs, showers).
Check:  uv run python -m blueprints.parsers.swiss
"""
import re
import struct
from collections import Counter
from pathlib import Path

from ..schemas import Entity, Page, Scene
from .swiss_fixtures import fixtures
from .swiss_openings import openings

NUMBER = re.compile(r"U?\d+\.\d+")
AREA = re.compile(r"\d+\.\d+ m²")  # printed area under some numbers; the geometry is the source of truth
MARKER = re.compile(r"(?:Whg|App)\. (\S+)")  # apartment marker above a label: "Whg. A" (de), "App. A" (fr, it)
# Every label drawn on the PDF's outdoor grey across the 1,040 plans (de, fr, it).
OUTDOOR = {"balkon", "balcon", "balcone", "loggia", "terrasse", "terrazza", "sitzplatz", "garten", "patio",
           "wintergarten", "jardin d'hiver", "giardino d'inverno"}
# The renderer's fills: outdoor spaces (also the ones the DXF leaves unlabelled) and shared spaces (stairwells,
# common corridors, cellars), which a unit outline sometimes encloses but gold apartment areas leave out.
OUTDOOR_GREY, SHARED_GREY = 0.894, 0.953


def _png_size(png: Path) -> tuple[int, int]:
    with png.open("rb") as f:
        return struct.unpack(">II", f.read(24)[16:24])


def _read_pdf(pdf: Path, anchors: dict[str, tuple[float, float]]):
    """The DXF-metres -> 0..1 page transform, and the page's grey fills as 0..1 polygons keyed by grey level.
    The PDF is the DXF drawn at 1:100 or 1:200 on A3, y flipped; the PNG is that page at 150 dpi.
    The transform is fitted on room texts that occur once in both files."""
    import pymupdf
    from shapely.geometry import Polygon

    page = pymupdf.open(pdf)[0]
    w, h = page.rect.width, page.rect.height
    spans = [s for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for s in line["spans"]]
    count = Counter(s["text"].strip() for s in spans)
    # centred text: bbox middle and baseline in the PDF, insert point in the DXF
    pairs = [(anchors[t], ((s["bbox"][0] + s["bbox"][2]) / 2, s["origin"][1]))
             for s in spans if (t := s["text"].strip()) in anchors and count[t] == 1]
    n = len(pairs)
    mx, my = sum(p[0][0] for p in pairs) / n, sum(p[0][1] for p in pairs) / n
    mX, mY = sum(p[1][0] for p in pairs) / n, sum(p[1][1] for p in pairs) / n
    # least squares for X = a x + b, Y = -a y + c (one scale, y flipped)
    a = sum((x - mx) * (X - mX) - (y - my) * (Y - mY) for (x, y), (X, Y) in pairs) / \
        sum((x - mx) ** 2 + (y - my) ** 2 for (x, y), _ in pairs)
    b, c = mX - a * mx, mY + a * my
    clip = lambda v: min(max(v, 0.0), 1.0)

    fills = {OUTDOOR_GREY: [], SHARED_GREY: []}
    for d in page.get_drawings():
        fill = d.get("fill")
        grey = round(fill[0], 3) if fill and max(fill) - min(fill) < 0.001 else None
        if grey in fills:
            pts = [p for it in d["items"] for p in it[1:] if isinstance(p, pymupdf.Point)] + \
                  [p for it in d["items"] if it[0] == "re" for p in (it[1].tl, it[1].tr, it[1].br, it[1].bl)]
            if len(pts) >= 3:
                fills[grey].append(Polygon([(p.x / w, p.y / h) for p in pts]).buffer(0))
    return (lambda x, y: (clip((a * x + b) / w), clip((c - a * y) / h))), fills


def _render(doc, path: str):
    """For an uploaded DXF with no sibling PDF or PNG: draw the modelspace onto a page the size of its extents.
    The page covers the extents exactly, so DXF -> 0..1 page coordinates is linear, with y flipped."""
    import hashlib

    import pymupdf
    from ezdxf import bbox
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    from .. import pipeline

    msp = doc.modelspace()
    ext = bbox.extents(msp)
    if not ext.has_data or not ext.size.x or not ext.size.y:
        raise NotImplementedError("the DXF modelspace is empty")
    (x0, y0, _), (w, h, _) = ext.extmin, ext.size
    backend = PyMuPdfBackend()
    Frontend(RenderContext(doc), backend, config.Configuration(background_policy=config.BackgroundPolicy.WHITE)) \
        .draw_layout(msp)
    mm_per_unit = 400 / max(w, h)  # a 400 mm long side whatever the drawing's units
    page = layout.Page(w * mm_per_unit, h * mm_per_unit, layout.Units.mm, margins=layout.Margins.all(0))
    png = backend.get_pixmap_bytes(page, fmt="png", settings=layout.Settings(fit_page=False, scale=mm_per_unit), dpi=150)
    image = f"pages/{hashlib.md5(path.encode()).hexdigest()}.png"
    (pipeline.CACHE / "pages").mkdir(parents=True, exist_ok=True)
    (pipeline.CACHE / image).write_bytes(png)
    pix = pymupdf.Pixmap(png)
    clip = lambda v: min(max(v, 0.0), 1.0)
    return (lambda x, y: (clip((x - x0) / w), clip((y0 + h - y) / h))), Page(image=image, width=pix.width, height=pix.height)


def build_scene(path: str, file: Path) -> Scene:
    """A dataset plan (file is the .pdf; the same-name .dxf and .png sit beside it) or an uploaded .dxf on its own."""
    import ezdxf
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    upload = file.suffix.lower() == ".dxf"
    doc = ezdxf.readfile(file.with_suffix(".dxf"))
    msp = doc.modelspace()
    if upload:  # an unknown file: cover only what it has layers for, so a missing layer is never a confident zero
        has = lambda *layers: all(n in doc.layers for n in layers)
        covers = ["room"] * has("ROOM_OUTLINE", "ROOM_TEXT") + ["apartment"] * has("UNIT_OUTLINE", "ROOM_TEXT") + \
                 ["door"] * has("DOOR") + ["window"] * has("WINDOW") + \
                 [f"fixture:{s}" for s in ("toilet", "sink", "bathtub", "shower") if has("FIXTURE")]
        if not covers:
            raise NotImplementedError("this DXF reader knows the layers ROOM_OUTLINE, ROOM_TEXT, UNIT_OUTLINE, DOOR, "
                                      f"WINDOW and FIXTURE; the file has {', '.join(sorted(l.dxf.name for l in doc.layers))}")
    else:  # dataset plans: a missing DOOR/WINDOW layer means none
        covers = ["room", "apartment", "door", "window",
                  "fixture:toilet", "fixture:sink", "fixture:bathtub", "fixture:shower"]
    polys = lambda layer: [(Polygon(e.get_points("xy")), e.dxf.handle)
                           for e in msp.query(f'LWPOLYLINE[layer=="{layer}"]') if len(e) >= 3]
    outlines, units = polys("ROOM_OUTLINE"), polys("UNIT_OUTLINE")

    # ROOM_TEXT reads in drawing order: [marker] label number [area]. A number closes a room.
    # A marker can sit just outside its unit outline; then its room's number point locates it.
    rooms, pending, markers, seen = [], [], [], {}
    for e in msp.query('TEXT[layer=="ROOM_TEXT"]'):
        text, (x, y) = e.dxf.text.strip(), (e.dxf.insert.x, e.dxf.insert.y)
        seen[text] = None if text in seen else (x, y)
        if NUMBER.fullmatch(text):
            rooms.append((pending[-1] if pending else None, text, x, y))
            if markers and markers[-1][2] is None:
                markers[-1][2] = (x, y)
            pending = []
        elif m := MARKER.fullmatch(text):
            markers.append([m[1], (x, y), None])
        elif not AREA.fullmatch(text):
            pending.append(text)

    def unit_of(x, y):
        return next((i for i, (u, _) in enumerate(units) if u.contains(Point(x, y))), None)

    unit_label = {}
    lost = []
    for letter, at, below in markers:
        i = unit_of(*at)
        if i is None and below:
            i = unit_of(*below)
        if i is None:
            lost.append((letter, at))
        else:
            unit_label.setdefault(i, letter)
    for letter, at in lost:  # neither point inside a unit: the nearest unmarked unit within a metre
        free = [(units[j][0].distance(Point(at)), j) for j in range(len(units)) if j not in unit_label]
        if free and min(free)[0] < 1:
            unit_label[min(free)[1]] = letter

    if upload:  # no PDF, so no grey fills: outdoor rooms come from labels only, shared spaces stay in their unit
        (to_page, page), fills = _render(doc, path), {OUTDOOR_GREY: [], SHARED_GREY: []}
    else:
        to_page, fills = _read_pdf(file, {t: xy for t, xy in seen.items() if xy})
        width, height = _png_size(file.with_suffix(".png"))
        page = Page(image=path[:-4] + ".png", width=width, height=height)
    shape =lambda poly: [to_page(x, y) for x, y in poly.exterior.coords[:-1]]

    entities = []
    # Only marked units are apartments: unmarked outlines are detached balconies or non-residential units
    # (offices, shops), which the gold apartment count leaves out but the total indoor area includes.
    for i, letter in unit_label.items():
        u, handle = units[i]
        entities.append(Entity(id=f"apt-{letter}", type="apartment", label=letter, geom=shape(u), ref=f"dxf:{handle}"))
    def net_area(poly):
        # The DXF drops polygon holes: a hole is drawn as a separate outline lying inside the room.
        holes = [p for p, _ in outlines if p is not poly and p.within(poly)]
        return (poly.difference(unary_union(holes)) if holes else poly).area

    def filled(geom, grey):
        if len(geom) < 3:
            return False
        poly = Polygon(geom).buffer(0)
        return sum(poly.intersection(f).area for f in fills[grey]) > 0.5 * poly.area

    ids, placed = set(), []  # placed: (outline in DXF metres, room entity) for locating doors, windows, fixtures
    for label, num, x, y in rooms:
        hits = [(p, h) for p, h in outlines if p.contains(Point(x, y))]
        poly, handle = min(hits, key=lambda ph: ph[0].area) if hits else (None, None)
        i = unit_of(x, y)
        rid, k = f"room-{num}", 1
        while rid in ids:  # duplicate numbers on one floor
            k += 1
            rid = f"room-{num}-{k}"
        ids.add(rid)
        geom = shape(poly) if poly else []
        entities.append(room := Entity(
            id=rid, type="room", label=label, number=num,
            apartment=None if filled(geom, SHARED_GREY) else unit_label.get(i),
            outdoor=(label or "").casefold() in OUTDOOR or filled(geom, OUTDOOR_GREY),
            geom=geom, qty={"m2": round(net_area(poly), 2)} if poly else {},
            ref=f"dxf:{handle}" if handle else None))
        if poly:
            placed.append((poly, room))

    def located(kind, pts, copies):
        """apartment and attrs for a door, window or fixture: the room(s) it is in or opens into.
        A fixture's box centre lies inside its room. A door or window box fills a wall gap and its long sides lie on
        the outlines of the rooms either side, so it touches its rooms rather than lying in one."""
        box = Polygon(pts)
        if kind == "fixture":
            sides = sorted((pr for pr in placed if pr[0].contains(box.centroid)), key=lambda pr: pr[0].area)[:1]
        else:  # the rooms sharing most edge with the box: two for a door, one for a window
            near = box.buffer(0.05)
            sides = sorted((pr for pr in placed if pr[0].intersects(near)), key=lambda pr: -pr[0].intersection(near).area)
            if kind == "window":  # a window between a room and its balcony belongs to the room
                sides = sorted(sides, key=lambda pr: pr[1].outdoor)[:1]
            # an entrance door belongs to its apartment, not the shared corridor it opens onto
            sides = sorted(sides[:2], key=lambda pr: pr[1].apartment is None)
        attrs = {"copies": copies} if copies > 1 else {}
        for k, (_, r) in zip(("", "2"), sides):
            attrs[f"room{k}"] = r.number
            if r.label:
                attrs[f"room_label{k}"] = r.label
        return dict(apartment=sides[0][1].apartment if sides else None, attrs=attrs)

    found = Counter()
    for kind, pts, ref, copies in openings(msp):
        found[kind] += 1
        entities.append(Entity(id=f"{kind}-{found[kind]}", type=kind, geom=[to_page(x, y) for x, y in pts], ref=ref,
                               **located(kind, pts, copies)))
    for sub, pts, ref, copies in fixtures(msp):
        found[sub] += 1
        entities.append(Entity(id=f"fixture-{sub}-{found[sub]}", type="fixture", subtype=sub,
                               geom=[to_page(x, y) for x, y in pts], ref=ref, **located("fixture", pts, copies)))
    return Scene(path=path, builder="swiss_dxf_upload@1" if upload else "swiss_dxf@6", tier="parse",
                 covers=covers, pages=[page], entities=entities)


if __name__ == "__main__":
    from .. import pipeline

    s = pipeline.build_scene("data/swiss_dwellings/site3387_bldg5238_floor23498.pdf")
    rooms = {e.number: e for e in s.entities if e.type == "room"}
    assert (rooms["7.01"].qty["m2"], rooms["7.16"].qty["m2"], rooms["7.08"].qty["m2"]) == (15.47, 5.63, 15.66)
    area = lambda **f: round(sum(e.qty["m2"] for e in rooms.values()
                                 if not e.outdoor and all(getattr(e, k) == v for k, v in f.items())), 2)
    assert (area(), area(apartment="A"), area(apartment="B")) == (212.4, 107.4, 80.2)  # gold 212.41: sum before rounding
    assert sorted(e.id for e in s.entities if e.type == "apartment") == ["apt-A", "apt-B"]
    assert Counter(e.subtype for e in s.entities if e.type == "fixture") == {"toilet": 4, "sink": 4, "bathtub": 2, "shower": 1}
    room = lambda p, n: next(e for e in pipeline.build_scene(f"data/swiss_dwellings/{p}.pdf").entities if e.number == n)
    assert room("site2155_bldg3005_floor13704", "4.11").qty["m2"] == 14.7  # outline minus the hole outline inside it
    assert room("site2135_bldg2978_floor13581", "2.01").outdoor  # unlabelled balcony, known by its grey fill

    # Doors, windows and fixtures carry their apartment and room, as counted on the PNG, through the executor.
    from ..executor import execute
    from ..schemas import Query, QueryPlan

    def count(formula=None, **queries):
        return execute(QueryPlan(queries={k: Query(op="count", **q) for k, q in queries.items()}, formula=formula,
                                 unit="count"), s).answer
    fix = lambda sub, apt, **where: count(n=dict(type="fixture", subtype=sub, apartment=apt, where=where))
    # A: Bad 7.05 (sink, toilet, shower tray) and Bad 7.07 (tub, toilet, sink); B: Bad 7.17 (toilet, sink), 7.18 (tub, toilet, sink)
    assert [fix(k, "A") for k in ("toilet", "sink", "bathtub", "shower")] == [2, 2, 1, 1]
    assert [fix(k, "B") for k in ("toilet", "sink", "bathtub", "shower")] == [2, 2, 1, 0]
    assert fix("toilet", "A", room_label="Bad") == 2 and fix("toilet", "A", room="7.07") == 1
    windows = lambda n: count(n=dict(type="window", where={"room": n}))
    assert (windows("7.08"), windows("7.20"), windows("7.03"), windows("7.22")) == (2, 2, 0, 1)  # 7.03 opens via a balcony door
    doors = lambda n: count("a + b", a=dict(type="door", where={"room": n}), b=dict(type="door", where={"room2": n}))
    assert (doors("7.06"), doors("7.05"), doors("7.03")) == (5, 1, 1)  # corridor A: entrance, Bad, 3 bedrooms
    # the entrance doors onto shared corridor 7.24 belong to their apartments; the lift door to no apartment
    assert [count(n=dict(type="door", apartment=a)) for a in ("A", "B")] == [9, 8]
    assert [e.id for e in s.entities if e.type == "door" and e.apartment is None] == ["door-18"]
    print("ok")
