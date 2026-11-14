"""Lane A. IFC model -> Scene, for ifc_models, ifc_bench and .ifc uploads.

A question may name the model (.ifc) or a plan rendered from it (<model>_plan-<level>.pdf). Both resolve to the
same model, and some PDF questions ask about the whole model, so always build from the .ifc.

One page per storey, drawn here from the model's own footprints (spaces, walls, doors, windows seen from above), so
an uploaded model with no plan beside it shows the same way as a dataset one. Quantities come from the model's
quantity sets; a room without an area quantity gets the area of its footprint. Every room with a footprint also gets
its perimeter and its wall area net of the doors and windows on its outline (outlines.measure), at its own height.
A model with no IfcSpace at all gets the rooms its walls enclose (rooms_from_walls), marked area_source "walls".

Check:  uv run --extra parsers python -m blueprints.parsers.ifc
"""
import hashlib
import math
import re
from functools import lru_cache
from pathlib import Path

from ..schemas import Entity, Page, Scene

BUILDER = "ifc@3"  # @3: rooms carry perimeter and net wall area; a model with no spaces gets rooms from its walls
SIDE = 2048  # long side of a storey page, pixels
# IFC class -> entity type. Stairs, beams and light fixtures are "other", told apart by attrs.category.
TYPES = {"IfcSpace": "room", "IfcDoor": "door", "IfcWindow": "window", "IfcWall": "wall", "IfcSlab": "slab",
         "IfcColumn": "column", "IfcStair": "other", "IfcBeam": "other", "IfcFlowTerminal": "fixture"}
FIXTURE_TYPES = {"TOILETPAN": "toilet", "WCSEAT": "toilet", "URINAL": "toilet", "WASHHANDBASIN": "sink", "SINK": "sink",
                 "BATH": "bathtub", "SHOWER": "shower"}
FIXTURE_NAMES = [(re.compile(r"toilet|\bwc\b|closet|urinal", re.I), "toilet"), (re.compile(r"shower", re.I), "shower"),
                 (re.compile(r"bath ?tub|\bbath\b", re.I), "bathtub"), (re.compile(r"sink|basin|lavatory|lavabo", re.I), "sink")]
QTY = {"wall": [("m", ("Length",)), ("m2", ("NetSideArea",)), ("m3", ("NetVolume",))],
       "slab": [("m3", ("NetVolume",))]}  # rooms: see room_area


def room_area(qtos: dict[str, dict], footprint: float | None) -> tuple[float | None, str]:
    """A space's net floor area: NetFloorArea, else the footprint of its body, else another floor-area quantity.

    The footprint comes before other quantities because it matched the gold on every model tried, while vendor
    quantities did not (the clinic's "GSA BIM Area" is 2-10% larger: it is measured to the wall centres)."""
    values = {k: v for q in qtos.values() for k, v in q.items() if isinstance(v, (int, float)) and k != "id"}
    if values.get("NetFloorArea"):
        return values["NetFloorArea"], "NetFloorArea"
    if footprint:
        return footprint, "footprint"
    areas = sorted((k for k in values if k.endswith("Area") and "Wall" not in k and "Ceiling" not in k and values[k] > 0),
                   key=lambda k: (k != "GrossFloorArea", "Floor" not in k, k))
    return (values[areas[0]], areas[0]) if areas else (None, "none")


def fixture_kind(predefined: str | None, *names: str | None) -> str:
    """toilet, sink, bathtub or shower from the IFC predefined type, else from the element or type name; else other."""
    if predefined in FIXTURE_TYPES:
        return FIXTURE_TYPES[predefined]
    text = " ".join(n for n in names if n)
    return next((kind for pattern, kind in FIXTURE_NAMES if pattern.search(text)), "other")


def projector(bounds: tuple[float, float, float, float]):
    """Model metres (x east, y north) -> (to_page, width, height, frame): 0..1 page coordinates, north up, long side
    SIDE px. frame says where the page sits in the model: its west edge, north edge, width and height in metres."""
    x0, y0, x1, y1 = bounds
    pad = 0.03 * max(x1 - x0, y1 - y0, 1.0)
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w, h = x1 - x0, y1 - y0
    scale = SIDE / max(w, h)
    clamp = lambda v: min(1.0, max(0.0, v))
    frame = {"frame_x_m": round(x0, 3), "frame_y_m": round(y1, 3), "frame_w_m": round(w, 3), "frame_h_m": round(h, 3)}
    return (lambda x, y: (round(clamp((x - x0) / w), 5), round(clamp((y1 - y) / h), 5))), max(1, round(w * scale)), max(1, round(h * scale)), frame


def _storey(element, get_container, get_aggregate):
    """The storey an element or space sits on, through containment and aggregation."""
    e = element
    for _ in range(8):
        parent = get_container(e) or get_aggregate(e)
        if parent is None:
            return None
        if parent.is_a("IfcBuildingStorey"):
            return parent
        e = parent
    return None


def _footprints(model, elements) -> dict[int, object]:
    """Element id -> its shape seen from above (a shapely polygon in model metres), for elements with a body."""
    import multiprocessing

    import ifcopenshell.geom
    from shapely.geometry import MultiPoint, Polygon
    from shapely.ops import unary_union

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("disable-opening-subtractions", True)  # plan outlines only; cutting openings is the slow part
    out = {}
    if not elements:
        return out
    it = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count(), include=elements)
    if not it.initialize():
        return out
    while True:
        shape = it.get()
        v, f = shape.geometry.verts, shape.geometry.faces
        xy = [(v[i], v[i + 1]) for i in range(0, len(v), 3)]
        if model.by_id(shape.id).is_a("IfcSpace") or model.by_id(shape.id).is_a("IfcWall"):
            # The union of the triangles keeps an L-shaped room or wall; a hull would fill its corner.
            tris = [Polygon([xy[f[i]], xy[f[i + 1]], xy[f[i + 2]]]) for i in range(0, len(f), 3)]
            poly = unary_union([t for t in tris if t.area > 1e-9]).buffer(0.005).buffer(-0.005)
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda g: g.area)
        else:
            poly = MultiPoint(xy).convex_hull
        if poly.geom_type == "Polygon" and poly.area > 1e-6:
            out[shape.id] = poly.simplify(0.01)
        if not it.next():
            return out


def _across(shape, width: float = 0.0, reach: float = 0.3):
    """A door or window footprint as outlines.surface wants it: stretched up to `reach` metres across its wall on each
    side, because the body sits inside the wall and a room's outline is the wall's face; and as long as the opening
    itself (`width`, the model's OverallWidth) when the body is longer, because a Revit door's body includes its trim.
    Never wider across than along, so its long side stays the opening's width."""
    from shapely.geometry import LineString
    c = list(shape.minimum_rotated_rectangle.exterior.coords)[:4]
    if math.dist(c[0], c[1]) < math.dist(c[1], c[2]):
        c = c[1:] + c[:1]  # c0-c1 is now a long side
    mid = lambda p, q, t=0.5: (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
    a, b, short = mid(c[0], c[3]), mid(c[1], c[2]), math.dist(c[1], c[2])
    long = math.dist(a, b)
    if 0 < width < long:
        a, b, long = mid(a, b, (1 - width / long) / 2), mid(b, a, (1 - width / long) / 2), width
    return LineString([a, b]).buffer(short / 2 + min(reach, max(long - short, 0) / 2), cap_style="flat")


def rooms_from_walls(mass: list) -> list:
    """The rooms a storey's wall footprints enclose, largest first, for a model with no IfcSpace. Footprints keep walls
    solid through doors and windows, so a room is closed; wall ends up to 30 cm apart are joined (resbim-100 leaves
    10 cm between two walls and loses its main room without it; 15 cm still loses it on resbim-250 and -400).
    Where the model has spaces to compare (KIT institute: 82 of 82 the same), a walled room matches its space; spaces
    with no wall between them (an open kitchen and living room) come out as one room."""
    from ..outlines import faces
    return sorted(faces(mass, close=0.3, min_area=1.0, min_width=0.4),
                  key=lambda f: (-round(f.area, 2), *(round(v, 2) for v in f.bounds)))  # the same order on every build


def _draw(file: Path, size: tuple[int, int], shapes: list[tuple[str, list]]) -> None:
    """A plain plan drawing: rooms pale, walls dark, doors and windows outlined."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    style = {"room": ("#eef2f6", "#9aa5b1", 1), "slab": (None, "#d5d9de", 1), "wall": ("#3b3f45", "#3b3f45", 1),
             "column": ("#3b3f45", "#3b3f45", 1), "door": ("#ffffff", "#b5651d", 2), "window": ("#ffffff", "#1f6fb2", 2),
             "fixture": ("#ffffff", "#555555", 1), "other": (None, "#888888", 1)}
    for kind in ("slab", "room", "wall", "column", "other", "fixture", "window", "door"):  # back to front
        fill, line, width = style[kind]
        for k, geom in shapes:
            if k == kind and len(geom) >= 3:
                d.polygon([(x * w, y * h) for x, y in geom], fill=fill, outline=line, width=width)
    file.parent.mkdir(parents=True, exist_ok=True)
    img.save(file, optimize=True)


def build_scene(path: str, file: Path) -> Scene:
    """A dataset model or plan PDF, or an uploaded .ifc. A plan <model>_plan-<level>.pdf gets its model's scene."""
    model_path = path
    if file.suffix.lower() == ".pdf":
        model_path, file = path.rsplit("_plan-", 1)[0] + ".ifc", file.with_name(file.name.rsplit("_plan-", 1)[0] + ".ifc")
    return _build(model_path, file).model_copy(update={"path": path}, deep=True)


def model_glb(path: str, file: Path) -> Path:
    """The model's own 3D geometry as a GLB for the 3D view, written once to .cache/models/. Every element with a body
    except spaces and opening voids, openings cut, one node per element named by its GlobalId. Metres, y up, and
    (x, z) = (east, -north), which is how the 3D view lays out the storey pages."""
    import multiprocessing

    import ifcopenshell
    import ifcopenshell.geom

    from .. import pipeline
    if file.suffix.lower() == ".pdf":
        path, file = path.rsplit("_plan-", 1)[0] + ".ifc", file.with_name(file.name.rsplit("_plan-", 1)[0] + ".ifc")
    out = pipeline.CACHE / "models" / (hashlib.md5(path.encode()).hexdigest() + ".glb")
    if out.exists():
        return out
    try:
        model = ifcopenshell.open(str(file))
    except Exception as e:
        raise ValueError(f"{file.name} is not a readable IFC model") from e
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".part.glb")  # the serializer picks its format from the extension
    settings = ifcopenshell.geom.settings()
    settings.set("use-element-guids", True)
    settings.set("apply-default-materials", True)
    writer = ifcopenshell.geom.serializers.glb(str(part), settings)
    writer.setFile(model)
    writer.setUnitNameAndMagnitude("METER", 1.0)
    writer.writeHeader()
    skip = [*model.by_type("IfcSpace"), *model.by_type("IfcOpeningElement")]
    shapes = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count(), exclude=skip)
    if shapes.initialize():
        while True:
            writer.write(shapes.get())
            if not shapes.next():
                break
    writer.finalize()
    del writer  # closes the file, so it can be renamed on Windows
    part.replace(out)
    return out


@lru_cache(maxsize=1)  # a model's plan PDFs are asked about one after another; build the model once for all of them
def _build(path: str, file: Path) -> Scene:
    import ifcopenshell
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.unit as uu

    from .. import pipeline
    from ..outlines import DOOR_H_M, WINDOW_H_M, measure
    try:
        model = ifcopenshell.open(str(file))
    except Exception as e:  # not STEP, or a schema IfcOpenShell does not know: say so instead of a 500
        raise NotImplementedError(f"not a readable IFC model: {e}") from None
    unit = {u: uu.calculate_unit_scale(model, t) for u, t in (("m", "LENGTHUNIT"), ("m2", "AREAUNIT"), ("m3", "VOLUMEUNIT"))}
    storeys = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: (s.Elevation or 0, s.Name or ""))
    elements = [e for cls in TYPES for e in model.by_type(cls)]
    kinds = {}
    for e in elements:
        kind = next(TYPES[c] for c in TYPES if e.is_a(c))
        if kind == "fixture":  # IFC4 IfcSanitaryTerminal, or an IFC2X3 IfcFlowTerminal typed as sanitary or light
            t = ue.get_type(e)
            if e.is_a("IfcSanitaryTerminal") or (t and t.is_a("IfcSanitaryTerminalType")):
                kinds[e.id()] = "fixture"
            elif e.is_a("IfcLightFixture") or (t and t.is_a("IfcLightFixtureType")):
                kinds[e.id()] = "other"
        else:
            kinds[e.id()] = kind
    # A part of a stair modelled as a stair (Molio: 564 risers and landings in 15 stairs) is not a stair of its own.
    part_of_same = lambda e: (p := ue.get_aggregate(e)) is not None and kinds.get(p.id()) == kinds[e.id()]
    elements = [e for e in elements if e.id() in kinds and not part_of_same(e)]
    shapes = _footprints(model, [e for e in elements if e.Representation])

    # One page per storey that has anything to draw, framed on its rooms and walls.
    on = {e.id(): _storey(e, ue.get_container, ue.get_aggregate) for e in elements}
    # Each storey's doors and windows as (footprint across its wall, height): what a room's wall area leaves out.
    holes: dict[int, list] = {}
    for e in elements:
        if kinds[e.id()] in ("door", "window") and e.id() in shapes and on[e.id()] is not None:
            w, h = ((getattr(e, a, None) or 0) * unit["m"] for a in ("OverallWidth", "OverallHeight"))
            holes.setdefault(on[e.id()].id(), []).append(
                (_across(shapes[e.id()], w), h if 0 < h < 10 else DOOR_H_M if kinds[e.id()] == "door" else WINDOW_H_M))
    pages, frame = [], {}
    for s in storeys:
        mine = [shapes[i] for i, st in on.items() if st == s and i in shapes]
        main = [shapes[i] for i, st in on.items() if st == s and i in shapes and kinds[i] in ("room", "wall")] or mine
        if not main:
            continue
        b = [g.bounds for g in main]
        to_page, w, h, where = projector((min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)))
        frame[s.id()] = (len(pages), to_page, where)
        pages.append(Page(image=f"pages/{hashlib.md5(path.encode()).hexdigest()}-{len(pages)}.png", width=w, height=h))

    level_of = {s.id(): s.Name or f"Storey {i + 1}" for i, s in enumerate(storeys)}
    entities, storey_height = [], {}
    for i, s in enumerate(storeys):
        nxt = storeys[i + 1].Elevation if i + 1 < len(storeys) else None
        attrs = {"elevation_m": round((s.Elevation or 0) * unit["m"], 3), **(frame[s.id()][2] if s.id() in frame else {})}
        if nxt is not None:
            attrs["height_m"] = storey_height[s.id()] = round((nxt - (s.Elevation or 0)) * unit["m"], 3)
        entities.append(Entity(id=f"level-{i + 1}", type="level", label=level_of[s.id()], page=frame.get(s.id(), (0,))[0],
                               attrs=attrs, ref=f"ifc:{s.GlobalId}"))
    drawn: dict[int, list] = {}
    for e in sorted(elements, key=lambda e: e.GlobalId):  # GlobalIds keep the ids stable across rebuilds
        kind, st = kinds[e.id()], on[e.id()]
        page, to_page = frame.get(st.id(), (0, None))[:2] if st else (0, None)
        poly = shapes.get(e.id())
        geom = [to_page(x, y) for x, y in poly.exterior.coords[:-1]] if poly is not None and to_page else []
        qtos = ue.get_psets(e, qtos_only=True)
        values = {k: v for q in qtos.values() for k, v in q.items() if isinstance(v, (int, float)) and k != "id"}
        qty, attrs = {}, {"ifc_class": e.is_a()}
        for u, keys in QTY.get(kind, []):
            if v := next((values[k] for k in keys if values.get(k)), None):
                qty[u] = round(v * unit[u], 4)
        t = ue.get_type(e)
        if t is not None and t.Name:
            attrs["type_name"] = t.Name
        if getattr(e, "PredefinedType", None) and e.PredefinedType != "NOTDEFINED":
            attrs["predefined_type"] = e.PredefinedType
        subtype = label = number = None
        if kind == "room":
            area, source = room_area(qtos, poly.area if poly is not None else None)
            if area:
                qty["m2"] = round(area * (unit["m2"] if source != "footprint" else 1), 4)
            attrs["area_source"] = source
            # The 3D view raises its walls to this: the room's own height, else floor to floor.
            if values.get("FinishCeilingHeight") or values.get("Height"):
                attrs["height_m"] = round((values.get("FinishCeilingHeight") or values["Height"]) * unit["m"], 3)
            elif st is not None and 0 < storey_height.get(st.id(), 0) < 10:
                attrs["height_m"] = storey_height[st.id()]
            if e.LongName:
                attrs["long_name"] = e.LongName
            number = e.Name
            # Questions name a space as "<LongName> (<Name>)", e.g. 'Living Room (A102)'.
            label = f"{e.LongName} ({e.Name})" if e.LongName and e.Name and e.LongName != e.Name else e.LongName or e.Name
        else:
            label = e.Name
            if kind == "other":  # plans count these with where={"category": ...}
                attrs["category"] = "stairs" if e.is_a("IfcStair") else "beam" if e.is_a("IfcBeam") else "light_fixture"
            if kind == "fixture":
                predefined = getattr(e, "PredefinedType", None) or getattr(t, "PredefinedType", None)
                subtype = fixture_kind(predefined, e.Name, t.Name if t else None, e.ObjectType)
        if st is not None and st.id() in frame and geom:
            drawn.setdefault(page, []).append((kind, geom))
        entities.append(entity := Entity(id=f"{kind}-{e.GlobalId}", type=kind, subtype=subtype, label=label, number=number,
                                         level=level_of[st.id()] if st else None, page=page, geom=geom, qty=qty,
                                         attrs=attrs, ref=f"ifc:{e.GlobalId}"))
        if kind == "room" and poly is not None:
            measure(entity, poly, openings=[o for o in holes.get(st.id(), ()) if o[0].intersects(poly)] if st else (),
                    height_m=attrs.get("height_m"))
    if not model.by_type("IfcSpace"):
        # Only then: a model with spaces is asked how many it has, and a storey it left without one has none.
        # A glazed front is the parts of a curtain wall (the wall itself has no body); a part wider than 0.5 m in plan
        # is sloped glazing, which would fill the room under it.
        # ponytail: a wall whose body is only in its parts (layered walls, e.g. the TU Wien model: 223 of 307) has no
        # footprint here, so its rooms leak; take the footprint from the parts when a space-less model needs it.
        glass = [p for cw in model.by_type("IfcCurtainWall") for p in (cw, *ue.get_decomposition(cw)) if p.Representation]
        glazed = [(_storey(model.by_id(i), ue.get_container, ue.get_aggregate), g)
                  for i, g in _footprints(model, glass).items() if 2 * g.area / g.length < 0.5]
        n = 0
        for s in storeys:
            if s.id() not in frame:
                continue
            page, to_page, _ = frame[s.id()]
            mass = [shapes[i] for i, st in on.items() if st == s and i in shapes and kinds[i] in ("wall", "column")]
            height = storey_height.get(s.id(), 0)
            for k, face in enumerate(rooms_from_walls(mass + [g for st, g in glazed if st == s]), 1):
                n += 1
                # conf: the outline is the walls' own, but it may hold several rooms and nothing names it.
                room = Entity(id=f"room-{s.GlobalId}-{k}", type="room", label=f"Room {n}", level=level_of[s.id()], page=page,
                              geom=[to_page(x, y) for x, y in face.exterior.coords[:-1]], qty={"m2": round(face.area, 4)},
                              attrs={"area_source": "walls", **({"height_m": height} if 0 < height < 10 else {})},
                              conf=0.8, ref=f"ifc:{s.GlobalId}")
                measure(room, face, openings=[o for o in holes.get(s.id(), ()) if o[0].intersects(face)],
                        height_m=room.attrs.get("height_m"))
                drawn.setdefault(page, []).append(("room", room.geom))
                entities.append(room)
    for i, page in enumerate(pages):
        _draw(pipeline.CACHE / page.image, (page.width, page.height), drawn.get(i, []))
    covers = ["level", "room", "door", "window", "wall", "slab", "column", "other", "fixture",
              "fixture:toilet", "fixture:sink", "fixture:bathtub", "fixture:shower"]
    return Scene(path=path, builder=BUILDER, tier="parse", covers=covers, pages=pages, entities=entities)


if __name__ == "__main__":
    assert room_area({"Qto_SpaceBaseQuantities": {"NetFloorArea": 12.5, "GrossFloorArea": 13.0, "id": 7}}, 11.0) == (12.5, "NetFloorArea")
    assert room_area({"GSA Space Areas": {"GSA BIM Area": 13.0}}, 11.0) == (11.0, "footprint")
    assert room_area({"BaseQuantities": {"GrossWallArea": 40.0, "GrossFloorArea": 13.0, "GSA BIM Area": 14.0}}, None) == (13.0, "GrossFloorArea")
    assert room_area({}, None) == (None, "none")
    assert fixture_kind("WCSEAT") == "toilet" and fixture_kind("NOTDEFINED", "Basin 600") == "sink"
    assert fixture_kind(None, "M_Bathtub-Rectangular") == "bathtub" and fixture_kind(None, "Floor drain") == "other"
    to_page, w, h, where = projector((0, 0, 10, 5))
    assert (w, h) == (SIDE, round(SIDE * 5.6 / 10.6)) and to_page(0, 5)[1] < to_page(0, 0)[1]  # north is up
    assert all(0 < v < 1 for v in to_page(5, 2.5))
    assert where == {"frame_x_m": -0.3, "frame_y_m": 5.3, "frame_w_m": 10.6, "frame_h_m": 5.6}
    from shapely.geometry import box
    # a 1 m door body with trim around a 0.8 m opening, 0.1 m deep: 0.8 m along the wall, 0.3 m across it each way
    assert [round(v, 2) for v in _across(box(0, 0, 1.0, 0.1), width=0.8).bounds] == [0.1, -0.3, 0.9, 0.4]
    assert [round(v, 2) for v in _across(box(0, 0, 0.5, 0.4)).bounds] == [0.0, -0.05, 0.5, 0.45]  # never wider across than along
    # two 4 x 3 m rooms whose outer wall stops 10 cm short of a corner
    walls = [box(0, 0, 8.4, 0.2), box(0, 3.2, 8.4, 3.4), box(0, 0.3, 0.2, 3.2), box(8.2, 0.2, 8.4, 3.2), box(4.1, 0.2, 4.3, 3.2)]
    assert [round(f.area, 1) for f in rooms_from_walls(walls)] == [11.7, 11.7]

    from .. import pipeline
    file = pipeline.DATA_ROOT / "data/ifc_models/bsc-duplex_duplex-a-20110907.ifc"
    if not file.exists():
        print(f"pure checks ok; skipped the model check: {file} not found")
        raise SystemExit
    s = build_scene("data/ifc_models/bsc-duplex_duplex-a-20110907.ifc", file)
    n = lambda kind, level=None: sum(e.type == kind and (level is None or e.level == level) for e in s.entities)
    assert (n("door"), n("window"), n("room"), n("wall")) == (14, 24, 21, 57)  # gold, whole model
    assert (n("door", "Level 1"), n("room", "Level 1")) == (6, 10)  # gold, per storey
    room = next(e for e in s.entities if e.label == "Living Room (A102)")
    assert abs(room.qty["m2"] - 27.66) <= 0.02 * 27.66 and room.level == "Level 1" and len(room.geom) >= 4
    # Bathroom 1 is 7.25 m around and 3.1 m high (floor to floor), less its one 0.762 x 2.032 m door.
    bath = next(e for e in s.entities if e.label == "Bathroom 1 (A104)")
    assert list(bath.qty) == ["m2", "m", "wall_m2"] and bath.qty["m"] == 7.25 and bath.attrs["wall_openings"] == 1
    assert abs(bath.qty["wall_m2"] - (7.25 * 3.1 - 0.762 * 2.032)) < 0.05 and not bath.attrs["wall_height_assumed"]
    # Bedroom 2: its door, a 2.8 m and a 0.75 m window and three 0.819 x 0.759 m windows (OverallWidth x OverallHeight).
    # Its IfcRelSpaceBoundary names one of the three small windows; all three stand on its outline.
    bed = next(e for e in s.entities if e.label == "Bedroom 2 (A203)")
    assert bed.attrs["wall_openings"] == 6
    assert abs(bed.attrs["wall_openings_m2"] - (0.864 * 2.032 + 2.8 * 2.41 + 0.75 * 2.2 + 3 * 0.819 * 0.759)) < 0.05

    # A model with no IfcSpace: rooms from its 14 walls. The 42.9 m2 main room is lost unless wall ends are joined.
    s = build_scene("data/ifc_models/resbim-100_model.ifc", file.with_name("resbim-100_model.ifc"))
    rooms = [e for e in s.entities if e.type == "room"]
    assert [round(e.qty["m2"], 1) for e in rooms] == [42.9, 14.9, 13.0, 6.9, 2.7], [e.qty for e in rooms]
    assert (rooms[0].label, rooms[0].conf, rooms[0].attrs["area_source"], rooms[0].attrs["wall_openings"]) == ("Room 1", 0.8, "walls", 6)
    assert all(e.qty["m"] > 0 and e.qty["wall_m2"] > 0 and len(e.geom) >= 4 for e in rooms)
    assert (n("door"), n("window"), n("wall")) == (6, 4, 14)  # what the model has, unchanged by the derived rooms
    print("ok")
