"""Lane A. IFC model -> Scene, for ifc_models, ifc_bench and .ifc uploads.

A question may name the model (.ifc) or a plan rendered from it (<model>_plan-<level>.pdf). Both resolve to the
same model, and some PDF questions ask about the whole model, so always build from the .ifc.

One page per storey, drawn here from the model's own footprints (spaces, walls, doors, windows seen from above), so
an uploaded model with no plan beside it shows the same way as a dataset one. Quantities come from the model's
quantity sets; a room without an area quantity gets the area of its footprint.

Check:  uv run --extra parsers python -m blueprints.parsers.ifc
"""
import hashlib
import re
from functools import lru_cache
from pathlib import Path

from ..schemas import Entity, Page, Scene

BUILDER = "ifc@2"  # @2: each level carries its page frame in metres, so the 3D view can stack the storeys
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


@lru_cache(maxsize=1)  # a model's plan PDFs are asked about one after another; build the model once for all of them
def _build(path: str, file: Path) -> Scene:
    import ifcopenshell
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.unit as uu

    from .. import pipeline
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
        entities.append(Entity(id=f"{kind}-{e.GlobalId}", type=kind, subtype=subtype, label=label, number=number,
                               level=level_of[st.id()] if st else None, page=page, geom=geom, qty=qty, attrs=attrs,
                               ref=f"ifc:{e.GlobalId}"))
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
    print("ok")
