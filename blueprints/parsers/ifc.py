"""Lane A. IFC model -> Scene, for ifc_models, ifc_bench and .ifc uploads.

A question may name the model (.ifc) or a plan rendered from it (<model>_plan-<level>.pdf). Both resolve to the
same model, and some PDF questions ask about the whole model, so always build from the .ifc.

One page per storey, drawn here from the model's own footprints (spaces, walls, doors, windows seen from above), so
an uploaded model with no plan beside it shows the same way as a dataset one. Quantities come from the model's
quantity sets; a room without an area quantity gets the area of its footprint, and a wall, slab, door, window or
room without a size gets it measured on its body (`sizes`; README.md, "Sizes"). attrs.size_source says which.

Check:  uv run --extra parsers python -m blueprints.parsers.ifc
"""
import hashlib
import re
from functools import lru_cache
from pathlib import Path

from ..schemas import Entity, Page, Scene

# @2: each level carries its page frame in metres, so the 3D view can stack the storeys
# @3: wall, slab, door, window and room sizes, from the quantity sets or else measured on the geometry
BUILDER = "ifc@3"
SIDE = 2048  # long side of a storey page, pixels
# IFC class -> entity type. Stairs, beams and light fixtures are "other", told apart by attrs.category.
TYPES = {"IfcSpace": "room", "IfcDoor": "door", "IfcWindow": "window", "IfcWall": "wall", "IfcSlab": "slab",
         "IfcColumn": "column", "IfcStair": "other", "IfcBeam": "other", "IfcFlowTerminal": "fixture"}
FIXTURE_TYPES = {"TOILETPAN": "toilet", "WCSEAT": "toilet", "URINAL": "toilet", "WASHHANDBASIN": "sink", "SINK": "sink",
                 "BATH": "bathtub", "SHOWER": "shower"}
FIXTURE_NAMES = [(re.compile(r"toilet|\bwc\b|closet|urinal", re.I), "toilet"), (re.compile(r"shower", re.I), "shower"),
                 (re.compile(r"bath ?tub|\bbath\b", re.I), "bathtub"), (re.compile(r"sink|basin|lavatory|lavabo", re.I), "sink")]
# Per entity type: where a size goes (a qty unit, or an attr in metres) and the quantities it is read from, first
# match wins. Rooms' areas: see room_area. Doors and windows take OverallWidth / OverallHeight, see sizes.
QTY = {"wall": [("m", ("Length",)), ("m2", ("NetSideArea",)), ("m3", ("NetVolume",)),
                ("height_m", ("Height",)), ("thickness_m", ("Width",))],
       "slab": [("m3", ("NetVolume",)), ("m2", ("NetArea", "GrossArea")), ("thickness_m", ("Thickness",))],  # see sizes
       "room": [("m", ("NetPerimeter", "GrossPerimeter"))],
       "door": [("m", ("OverallWidth",)), ("height_m", ("OverallHeight",))],
       "window": [("m", ("OverallWidth",)), ("height_m", ("OverallHeight",))]}


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


def strip(footprint) -> tuple[float, float] | None:
    """(length, thickness) of one piece of wall from its footprint, in the footprint's units.
    A straight wall, square or mitred at its ends: the thickness is the short side of the tightest box around it and
    the length its area over that, the mean of its two faces. A bent or curved wall: the strip with the footprint's
    area and perimeter, which is its length along the middle.
    ponytail: a stub shorter than it is thick reads the wrong way round; the wall's Axis curve would tell them apart."""
    import math

    area = footprint.area
    if area <= 0:
        return None
    if area > 0.99 * footprint.convex_hull.area:
        ring = list(footprint.minimum_rotated_rectangle.exterior.coords)
        thickness = min(math.dist(ring[0], ring[1]), math.dist(ring[1], ring[2]))
        return (area / thickness, thickness) if thickness > 0 else None
    p = footprint.exterior.length
    d = p * p - 16 * area
    if d < 0:  # rounder than a square: not a strip
        return None
    length = (p + d ** 0.5) / 4
    return length, area / length


def sizes(kind: str, values: dict, unit: dict, footprint=None, solid=None, cuts=()) -> tuple[dict, dict]:
    """(qty, attrs) of one element: each size from the model's own quantities when it has them, else measured on its
    geometry. values: its quantities by name, in model units (for a door or window OverallWidth and OverallHeight);
    unit: metres per model unit by qty unit; footprint: its shape from above, in metres (every piece of a wall);
    solid: (lowest z, highest z, volume, top area) of its body; cuts: (footprint, lowest z, highest z) of each
    opening in a wall. attrs.size_source says where the sizes came from, naming the measured ones when only some were."""
    import math

    z0, z1, volume, top = (solid or (0, 0, 0, 0))[:4]
    geometry = {}  # what the body gives, in metres
    if kind == "wall" and footprint is not None:
        pieces = [lt for lt in map(strip, getattr(footprint, "geoms", [footprint])) if lt]
        if pieces:
            geometry["m"] = sum(length for length, _ in pieces)
            thickness = geometry["thickness_m"] = sum(length * t for length, t in pieces) / geometry["m"]
            # Side = volume / thickness, so a gable end is not counted as a rectangle; less each opening's box inside
            # the wall. ponytail: within 2% of the kernel's own cut on 84-100% of walls over four models; it takes
            # too much where openings overlap or are not boxes. Cutting for real (a second geometry pass without
            # disable-opening-subtractions, for the walls that have openings and no NetSideArea) is the upgrade.
            if thickness > 0.01 and volume:
                holes = sum(footprint.intersection(f).area * max(0, min(z1, b) - max(z0, a)) for f, a, b in cuts)
                geometry["m2"] = max(0, volume - holes) / thickness
    if kind in ("wall", "door", "window") and z1 > z0:
        geometry["height_m"] = z1 - z0
    if kind in ("door", "window") and footprint is not None:  # the longer side of the tightest box around it
        ring = list(footprint.minimum_rotated_rectangle.exterior.coords)
        geometry["m"] = max(math.dist(ring[0], ring[1]), math.dist(ring[1], ring[2]))
    if kind == "slab" and top:
        geometry["m2"], geometry["thickness_m"] = top, volume / top
    if kind == "room" and footprint is not None:
        from .swiss import room_dims
        geometry["m"], width, length = room_dims(footprint)

    if kind == "slab":  # two slab quantities that exporters disagree on are checked against the body before use
        values = dict(values)
        # Thickness is Depth in IFC4's Qto_SlabBaseQuantities and Width in what Revit and ArchiCAD export, and either
        # can be a plan dimension or one layer instead: a stated one is taken only when the body agrees within 10%.
        t = geometry.get("thickness_m")
        values["Thickness"] = next((v for n in ("Depth", "Width") if (v := values.get(n)) and t and abs(v * unit["m"] - t) <= 0.1 * t), None)
        # A net area above the gross one is the slab's whole surface, not its top (sixty5_str states twice the top).
        gross = (values.get("GrossArea") or 0) * unit["m2"] or geometry.get("m2")
        if gross and (values.get("NetArea") or 0) * unit["m2"] > 1.02 * gross:
            del values["NetArea"]

    qty, attrs, measured = {}, {}, []
    for key, names in QTY.get(kind, []):
        read = next((values[n] for n in names if values.get(n)), None)
        value = read * unit[key if key in unit else "m"] if read else geometry.get(key)
        if not value or key == "m3" and not read:  # a volume is only ever the model's own
            continue
        if not read:
            measured.append(key if key.endswith("_m") else f"qty.{key}")
        (attrs if key.endswith("_m") else qty)[key] = round(value, 3 if key.endswith("_m") else 4)
    if qty or attrs:
        stated = "attributes" if kind in ("door", "window") else "quantity set"
        attrs["size_source"] = "geometry" if len(measured) == len(qty) + len(attrs) else stated if not measured else \
            f"{stated}; geometry: {', '.join(measured)}"
    if kind == "room" and footprint is not None:
        attrs["width_m"], attrs["length_m"] = width, length  # always the footprint's: no quantity names these
    return qty, attrs


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


def _footprints(model, elements) -> tuple[dict[int, object], dict[int, tuple]]:
    """Element id -> its shape seen from above (a shapely polygon in model metres), for elements with a body; and
    element id -> (lowest z, highest z, volume, top area, whole shape from above) of that body, in metres. Openings
    are not cut, so these are gross. The top area is a slab's: its plan area, over the cosine of its slope. The whole
    shape keeps every piece of a wall drawn in several, where the first dict keeps the largest."""
    import multiprocessing

    import numpy as np

    import ifcopenshell.geom
    from shapely.geometry import MultiPoint, Polygon
    from shapely.ops import unary_union

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    settings.set("disable-opening-subtractions", True)  # plan outlines only; cutting openings is the slow part
    out, solids = {}, {}
    if not elements:
        return out, solids
    it = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count(), include=elements)
    if not it.initialize():
        return out, solids
    while True:
        shape = it.get()
        v, f = shape.geometry.verts, shape.geometry.faces
        xy = [(v[i], v[i + 1]) for i in range(0, len(v), 3)]
        element, whole, top = model.by_id(shape.id), None, 0.0
        pts = np.array(v).reshape(-1, 3)
        a, b, c = (pts[np.array(f, dtype=int).reshape(-1, 3)[:, k]] for k in range(3))
        normal = np.cross(b - a, c - a)  # twice each triangle's area, along its normal
        if element.is_a("IfcSpace") or element.is_a("IfcWall") or element.is_a("IfcSlab"):
            # The union of the triangles keeps an L-shaped room or wall; a hull would fill its corner.
            tris = [Polygon([xy[f[i]], xy[f[i + 1]], xy[f[i + 2]]]) for i in range(0, len(f), 3)]
            whole = unary_union([t for t in tris if t.area > 1e-9]).buffer(0.005).buffer(-0.005)
        if element.is_a("IfcSlab"):  # drawn as its hull, as before; measured as its true outline
            poly = MultiPoint(xy).convex_hull
            twice = np.linalg.norm(normal, axis=1)
            flat = np.abs(normal[:, 2]) > 0.5 * twice  # faces lying flatter than 60 degrees: tops and undersides
            if flat.any():
                top = whole.area * float(twice[flat].sum() / np.abs(normal[flat, 2]).sum())
        elif whole is not None:
            poly = max(whole.geoms, key=lambda g: g.area) if whole.geom_type == "MultiPolygon" else whole
        else:
            poly = MultiPoint(xy).convex_hull
        if len(pts):
            solids[shape.id] = (float(pts[:, 2].min()), float(pts[:, 2].max()),
                                abs(float(np.einsum("ij,ij->", a - pts[0], normal))) / 6, top, whole)
        if poly.geom_type == "Polygon" and poly.area > 1e-6:
            out[shape.id] = poly.simplify(0.01)
        if not it.next():
            return out, solids


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
    # The openings cut into walls are measured too: a wall's net side area is its side less its openings.
    voids: dict[int, list] = {}
    for rel in model.by_type("IfcRelVoidsElement"):
        if kinds.get(rel.RelatingBuildingElement.id()) == "wall" and rel.RelatedOpeningElement.Representation:
            voids.setdefault(rel.RelatingBuildingElement.id(), []).append(rel.RelatedOpeningElement)
    shapes, solids = _footprints(model, [e for e in elements if e.Representation] + [o for os in voids.values() for o in os])

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
        if kind in ("door", "window"):  # their sizes are attributes of the element, not quantities
            values = {"OverallWidth": e.OverallWidth, "OverallHeight": e.OverallHeight}
        cuts = [(shapes[o.id()], *solids[o.id()][:2]) for o in voids.get(e.id(), []) if o.id() in shapes and o.id() in solids]
        solid = solids.get(e.id())
        qty, sized = sizes(kind, values, unit, solid[4] if kind == "wall" and solid else poly, solid, cuts)
        attrs = {"ifc_class": e.is_a()}
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
            attrs |= sized
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
            attrs |= sized
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

    # Sizes: the model's own when stated, else measured. A 5 x 0.2 m wall 3 m high with a 1 x 2 m door opening.
    from shapely.geometry import Polygon, box
    near = lambda a, b: abs(a - b) < 1e-6
    assert all(map(near, strip(box(0, 0, 5, 0.2)), (5, 0.2)))
    assert all(map(near, strip(Polygon([(0, 0), (5, 0), (4.8, 0.2), (0.2, 0.2)])), (4.8, 0.2)))  # mitred: mean of its faces
    assert all(map(near, strip(Polygon([(0, 0), (5, 0), (5, 3), (4.8, 3), (4.8, 0.2), (0, 0.2)])), (7.8, 0.2)))  # bent: along the middle
    m = {"m": 1, "m2": 1, "m3": 1}
    wall, solid, door = box(0, 0, 5, 0.2), (0, 3, 3.0, 0, None), [(box(2, -0.1, 3, 0.3), 0, 2)]
    assert sizes("wall", {}, m, wall, solid, door) == ({"m": 5, "m2": 13}, {"height_m": 3, "thickness_m": 0.2, "size_source": "geometry"})
    stated = sizes("wall", {"Length": 5200, "NetSideArea": 14, "NetVolume": 2.6}, {"m": 0.001, "m2": 1, "m3": 1}, wall, solid, door)
    assert stated == ({"m": 5.2, "m2": 14, "m3": 2.6}, {"height_m": 3, "thickness_m": 0.2,
                                                         "size_source": "quantity set; geometry: height_m, thickness_m"})
    assert sizes("door", {"OverallWidth": 900, "OverallHeight": 2100}, {"m": 0.001}, box(0, 0, 1.1, 0.2), (0, 2.2, 0, 0, None)) == \
        ({"m": 0.9}, {"height_m": 2.1, "size_source": "attributes"})
    assert sizes("door", {"OverallWidth": None, "OverallHeight": None}, m, box(0, 0, 0.9, 0.1), (0, 2.1, 0, 0, None))[0] == {"m": 0.9}
    slab = lambda **stated: sizes("slab", stated, m, None, (0, 0.2, 4.0, 20.0, None))
    assert slab() == ({"m2": 20}, {"thickness_m": 0.2, "size_source": "geometry"})
    assert slab(Depth=0.05, Width=0.2, NetArea=19, GrossArea=20) == ({"m2": 19}, {"thickness_m": 0.2, "size_source": "quantity set"})
    assert slab(Width=4.0, NetArea=41, GrossArea=20)[0] == {"m2": 20}  # Width is the plan width; NetArea the whole surface
    assert sizes("room", {"NetPerimeter": 14.5}, m, box(0, 0, 4, 3)) == \
        ({"m": 14.5}, {"size_source": "quantity set", "width_m": 3, "length_m": 4})

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
    # The duplex has no quantity sets, so every size is measured; its type names say what to expect.
    assert (room.qty["m"], room.attrs["width_m"], room.attrs["length_m"], room.attrs["size_source"]) == (21.132, 4.783, 5.783, "geometry")
    of = lambda kind: [e for e in s.entities if e.type == kind]
    assert all({"m", "m2"} <= e.qty.keys() and e.attrs["size_source"] == "geometry" for e in of("wall"))
    assert 300 < sum(e.qty["m"] for e in of("wall")) < 350 and all(0.05 <= e.attrs["thickness_m"] <= 0.6 for e in of("wall"))
    stud = [e for e in of("wall") if "152 mm Stud" in e.label]
    assert stud and all(e.attrs["thickness_m"] == 0.152 and e.qty["m2"] <= e.qty["m"] * e.attrs["height_m"] + 1e-3 for e in stud)
    door = next(e for e in of("door") if e.attrs["type_name"] == "0762 x 2032mm")  # OverallWidth, OverallHeight
    assert (door.qty, door.attrs["height_m"], door.attrs["size_source"]) == ({"m": 0.762}, 2.032, "attributes")
    assert all(0.5 <= e.qty["m"] <= 2.5 for e in of("door"))
    glazing = next(e for e in of("window") if e.attrs["type_name"] == "2800mm x 2410mm")
    assert (glazing.qty["m"], glazing.attrs["height_m"]) == (2.8, 2.41) and all("m" in e.qty for e in of("window"))
    grade = next(e for e in of("slab") if "150mm Exterior Slab" in e.label)
    assert grade.attrs["thickness_m"] == 0.15 and grade.qty["m2"] > 20 and all("m2" in e.qty for e in of("slab"))
    print("ok")
