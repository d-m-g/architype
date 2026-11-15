"""Room outlines and what they measure. Shared by every reader, so all formats agree on what a room is.

faces():   the rooms a wall mass encloses. The reader supplies the walls (IFC wall footprints, DXF wall lines given a
           thickness, PDF wall fills) and the door and window shapes, which are painted in as wall so a room stays
           closed across them.
surface(): a room outline's perimeter and its wall area net of the openings on it.
measure(): writes both onto a room entity as qty "m" (perimeter) and "wall_m2", so "how much paint for the bedrooms"
           is a sum the executor runs, with the room's own filters and the room as evidence.

Check:  uv run --extra parsers python -m blueprints.outlines
"""
import math

from .schemas import Entity

# ponytail: assumed where a drawing gives no heights (every format but IFC). The values are written on the room
# (attrs.wall_height_m, wall_height_assumed), so the answer card can show them; make them plan assumptions when
# users need to change them per question.
HEIGHT_M, DOOR_H_M, WINDOW_H_M = 2.5, 2.0, 1.2


def faces(walls, openings=(), close: float = 0.0, min_area: float = 0.0, min_width: float = 0.0) -> list:
    """The faces a wall mass encloses, largest first, in the units of the input (shapely polygons; a face keeps its
    holes, e.g. a column or a shaft standing in the room). Free space that reaches the outside is not a room.

    walls: shapely polygons (give lines a thickness with .buffer() first). openings: door and window shapes, painted
    in as wall. close: wall ends up to this far apart are joined (a modelling gap, not a doorway: anything narrower
    than `close` is filled, so keep it well under a corridor's width). min_width drops slivers such as the inside of a
    wall drawn as two lines."""
    from shapely.ops import unary_union

    solid = unary_union([g for g in (*walls, *openings) if not g.is_empty])
    if solid.is_empty:
        return []
    if close:  # morphological closing with square corners
        solid = solid.buffer(close / 2, join_style="mitre").buffer(-close / 2, join_style="mitre")
    x0, y0, x1, y1 = solid.bounds
    frame = solid.envelope.buffer(0.01 * max(x1 - x0, y1 - y0) + close)
    free = frame.difference(solid)
    out = [g for g in getattr(free, "geoms", [free])
           if g.geom_type == "Polygon" and not g.intersects(frame.exterior) and g.area >= min_area
           and not (min_width and g.buffer(-min_width / 2).is_empty)]
    return sorted(out, key=lambda g: -g.area)


def _long_side(shape) -> float:
    r = shape.minimum_rotated_rectangle
    if r.geom_type != "Polygon":
        return r.length
    c = list(r.exterior.coords)
    return max(math.dist(c[0], c[1]), math.dist(c[1], c[2]))


def surface(room, m_per_unit: float = 1.0, height_m: float = HEIGHT_M, openings=(), near_m: float = 0.05) -> dict:
    """What a room outline measures. room: a shapely polygon; openings: (shape, height in metres) for each door and
    window of the drawing, in the room's units. An opening counts for this room when it lies on the outline, and takes
    out its width along the outline times its height. Holes in the outline (a column) have wall surface too."""
    perimeter = room.length * m_per_unit
    cut, n = 0.0, 0
    for shape, h in openings:
        long = _long_side(shape)
        on = min(room.boundary.intersection(shape.buffer(near_m / m_per_unit)).length, long)
        if long and on > 0.3 * long:
            cut += on * m_per_unit * min(h, height_m)
            n += 1
    gross = perimeter * height_m
    return {"perimeter_m": perimeter, "gross_m2": gross, "openings_m2": cut, "net_m2": max(gross - cut, 0.0), "openings": n}


def measure(room: Entity, outline, m_per_unit: float = 1.0, openings=(), height_m: float | None = None) -> None:
    """Put the perimeter and net wall area on a room entity. height_m: the room's real height when the drawing gives
    it (IFC); otherwise HEIGHT_M is assumed and the room says so. A perimeter the room already has (a model's own
    NetPerimeter, a parser's measured one) is kept."""
    s = surface(outline, m_per_unit, height_m or HEIGHT_M, openings)
    room.qty.setdefault("m", round(s["perimeter_m"], 2))
    room.qty["wall_m2"] = round(s["net_m2"], 2)
    room.attrs |= {"wall_height_m": height_m or HEIGHT_M, "wall_height_assumed": height_m is None,
                   "wall_openings_m2": round(s["openings_m2"], 2), "wall_openings": s["openings"]}


if __name__ == "__main__":
    from shapely.geometry import box

    # Two 4.7 x 4.6 m rooms: an outer wall 0.2 thick and a partition with a 0.9 m doorway.
    outer = box(0, 0, 10, 5).difference(box(0.2, 0.2, 9.8, 4.8))
    walls = [outer, box(4.9, 0.2, 5.1, 2.0), box(4.9, 2.9, 5.1, 4.8)]
    door = box(4.9, 2.0, 5.1, 2.9)
    assert [round(f.area, 2) for f in faces(walls)] == [43.42]  # the doorway joins the rooms
    two = faces(walls, [door])
    assert [round(f.area, 2) for f in two] == [21.62, 21.62]
    # a 10 cm modelling gap in the outer wall lets the left room out, unless wall ends are joined
    leaky = [outer.difference(box(3, 0, 3.1, 0.2)), *walls[1:]]
    assert len(faces(leaky, [door])) == 1 and [round(f.area, 1) for f in faces(leaky, [door], close=0.15)] == [21.6, 21.6]
    # a wall drawn as two lines encloses a sliver that is not a room
    assert len(faces([*walls, box(6, 1, 8, 1.02), box(6, 1.1, 8, 1.12), box(6, 1, 6.02, 1.12), box(7.98, 1, 8, 1.12)], [door], min_width=0.3)) == 2
    assert faces([]) == []

    s = surface(two[0], 1.0, 2.5, [(door, DOOR_H_M)])
    assert (round(s["perimeter_m"], 2), round(s["gross_m2"], 2), round(s["openings_m2"], 2), round(s["net_m2"], 2), s["openings"]) == (18.6, 46.5, 1.8, 44.7, 1)
    assert surface(two[0], 1.0, 2.5, [(box(20, 20, 21, 20.2), 2.0)])["openings"] == 0  # a door elsewhere
    # the same room drawn in centimetres
    cm = surface(box(20, 20, 490, 480), 0.01, 2.5, [(box(490, 200, 510, 290), DOOR_H_M)])
    assert (round(cm["perimeter_m"], 2), round(cm["net_m2"], 2)) == (18.6, 44.7)
    room = Entity(id="room-1", type="room", qty={"m2": 21.62})
    measure(room, two[0], openings=[(door, DOOR_H_M)])
    assert room.qty == {"m2": 21.62, "m": 18.6, "wall_m2": 44.7} and room.attrs["wall_height_assumed"] is True
    measure(room, two[0], openings=[(door, DOOR_H_M)], height_m=3.0)
    assert room.qty["wall_m2"] == 54.0 and room.attrs["wall_height_assumed"] is False
    print("ok")
