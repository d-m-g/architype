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


def build_scene(path: str, file: Path) -> Scene:
    import ezdxf
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    msp = ezdxf.readfile(file.with_suffix(".dxf")).modelspace()
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

    to_page, fills = _read_pdf(file, {t: xy for t, xy in seen.items() if xy})
    shape = lambda poly: [to_page(x, y) for x, y in poly.exterior.coords[:-1]]

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

    ids = set()
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
        entities.append(Entity(
            id=rid, type="room", label=label, number=num,
            apartment=None if filled(geom, SHARED_GREY) else unit_label.get(i),
            outdoor=(label or "").casefold() in OUTDOOR or filled(geom, OUTDOOR_GREY),
            geom=geom, qty={"m2": round(net_area(poly), 2)} if poly else {},
            ref=f"dxf:{handle}" if handle else None))
    found = Counter()
    for kind, pts, ref, copies in openings(msp):
        found[kind] += 1
        entities.append(Entity(id=f"{kind}-{found[kind]}", type=kind, geom=[to_page(x, y) for x, y in pts], ref=ref,
                               attrs={"copies": copies} if copies > 1 else {}))
    for sub, pts, ref, copies in fixtures(msp):
        found[sub] += 1
        entities.append(Entity(id=f"fixture-{sub}-{found[sub]}", type="fixture", subtype=sub,
                               geom=[to_page(x, y) for x, y in pts], ref=ref,
                               attrs={"copies": copies} if copies > 1 else {}))
    png = file.with_suffix(".png")
    width, height = _png_size(png)
    return Scene(path=path, builder="swiss_dxf@5", tier="parse",
                 covers=["room", "apartment", "door", "window",
                         "fixture:toilet", "fixture:sink", "fixture:bathtub", "fixture:shower"],
                 pages=[Page(image=path[:-4] + ".png", width=width, height=height)], entities=entities)


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
    print("ok")
