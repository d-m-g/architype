"""Sanitary fixtures from a Swiss Dwellings DXF modelspace.

The FIXTURE layer holds loose primitives, no blocks. Every fixture is one closed 4-vertex box with its
symbol drawn inside, and the symbol's primitives name the kind:
  bathtub  a 69-vertex open polyline (the tub's rounded inner outline) and a drain circle
  toilet   a 48-vertex oval (the bowl) and a 2-vertex line (the cistern edge)
  sink     a 48-vertex oval (the basin) and no line, usually a drain circle
  shower   a 5-vertex open polyline (the tray's inner edge) and a drain circle
Kitchen counters (hob circles), stairs and lift shafts are boxes on this layer too, and some of them enclose
a fixture box, so each primitive belongs only to the smallest box around it.

Exact copies (identical box, new handle) count once, as for doors and windows; across the 1,040 plans there are none.
"""


def fixtures(msp) -> list[tuple[str, list[tuple[float, float]], str, int]]:
    """(subtype, box corners in DXF metres, ref, copies): one per drawn position, copies = boxes drawn there."""
    boxes, parts = [], []  # parts: (bbox, kind) with kind "P<vertices>" or "C"
    for e in msp.query('LWPOLYLINE[layer=="FIXTURE"]'):
        pts = [(float(x), float(y)) for x, y in e.get_points("xy")]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        if e.closed and len(pts) == 4:
            boxes.append((bbox, pts, e.dxf.handle, []))
        else:
            parts.append((bbox, f"P{len(pts)}"))
    for e in msp.query('CIRCLE[layer=="FIXTURE"]'):
        (x, y, _), r = e.dxf.center, e.dxf.radius
        parts.append(((x - r, y - r, x + r, y + r), "C"))

    area = lambda b: (b[2] - b[0]) * (b[3] - b[1])
    inside = lambda a, b: b[0] - 1e-3 <= a[0] and b[1] - 1e-3 <= a[1] and a[2] <= b[2] + 1e-3 and a[3] <= b[3] + 1e-3
    for bbox, kind in parts:
        owners = [b for b in boxes if inside(bbox, b[0])]
        if owners and kind != "P2":
            owners = [min(owners, key=lambda b: area(b[0]))]
        # A toilet box can overlap its neighbour so that the cistern line lies in both; lines go to every box
        # around them. Without the bowl a stair or counter box still classifies as nothing.
        for b in owners:
            b[3].append(kind)

    found: dict[tuple, list] = {}
    for _, pts, handle, kinds in boxes:
        if "P69" in kinds:
            sub = "bathtub"
        elif "P48" in kinds:
            sub = "toilet" if "P2" in kinds else "sink"
        elif "P5" in kinds and "C" in kinds:
            sub = "shower"
        else:
            continue
        # One plan redraws a tub outline 1-3 cm off inside the same box: one tub to a reader, two in the file.
        n = max(1, kinds.count("P69"))
        key = (sub, *pts)
        if key in found:
            found[key][3] += n
        else:
            found[key] = [sub, pts, f"dxf:{handle}", n]
    return [tuple(v) for v in found.values()]


if __name__ == "__main__":
    import ezdxf

    root = r"C:\Users\pradh\Downloads\junction-dataset\junction-dataset\data\swiss_dwellings"
    for name, want, copies in [  # (toilet, sink, bathtub, shower) as seen on the PNG; all equal gold except site543
        ("site3387_bldg5238_floor23498", (4, 4, 2, 1), 0),  # the shower is a tub-sized tray
        ("site164_bldg332_floor1360", (4, 2, 2, 0), 0),  # toilet boxes overlap, sharing a cistern line
        ("site338_bldg709_floor3009", (4, 6, 2, 2), 0),  # two sinks in 45° boxes; a stair box encloses a toilet
        ("site543_bldg1241_floor51691", (5, 5, 5, 0), 1),  # gold 6 tubs: one tub outline is drawn twice
        ("site603_bldg1318_floor5777", (0, 0, 0, 0), 0),  # FIXTURE layer empty (no file lacks the layer itself)
    ]:
        found = fixtures(ezdxf.readfile(rf"{root}\{name}.dxf").modelspace())
        got = tuple(sum(s == k for s, *_ in found) for k in ("toilet", "sink", "bathtub", "shower"))
        assert (got, sum(c - 1 for *_, c in found)) == (want, copies), (name, got)
    print("ok")
