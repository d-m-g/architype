"""Doors and windows from a Swiss Dwellings DXF modelspace.

Each door or window is drawn around one rectangle polyline on its layer: a door is a box (the frame)
plus its swing arc(s) and leaf line(s), a window is a closed box plus its glazing lines. One box is
one opening, so a double door (one box, two arcs) is one door.

Most plans also hold exact copies of some boxes (identical coordinates, new handle), about 11% of all
boxes. They render as one door, so we count each position once. The dataset's gold counts every copy,
so this deliberately scores lower on door and window questions: we count what a reader sees.
"""


def openings(msp) -> list[tuple[str, list[tuple[float, float]], str, int]]:
    """(kind, box corners in DXF metres, ref, copies): one per drawn position, copies = boxes drawn there."""
    found: dict[tuple, list] = {}
    for kind, layer in (("door", "DOOR"), ("window", "WINDOW")):
        for e in msp.query(f'LWPOLYLINE[layer=="{layer}"]'):
            pts = [(float(x), float(y)) for x, y, *_ in e.get_points()]
            # Window boxes are closed 4-vertex polylines; door boxes are open 5-vertex ones that end where they start.
            if e.closed and len(pts) == 4 or len(pts) == 5 and pts[0] == pts[-1]:
                key = (kind, *pts[:4])
                if key in found:
                    found[key][3] += 1
                else:
                    found[key] = [kind, pts[:4], f"dxf:{e.dxf.handle}", 1]
    return [tuple(v) for v in found.values()]


if __name__ == "__main__":
    import ezdxf

    root = r"C:\Users\pradh\Downloads\junction-dataset\junction-dataset\data\swiss_dwellings"
    for name, doors, windows in [
        ("site3387_bldg5238_floor23498", 18, 11),  # gold 20: 2 doors are drawn twice; 4 double doors
        ("site1601_bldg2794_floor12912", 16, 14),  # gold 18, 15
        ("site70_bldg21_floor88", 21, 13),  # gold 26, 14
        ("site3912_bldg6120_floor25531", 0, 1),  # no DOOR layer
        ("site613_bldg1328_floor11989", 8, 0),  # gold 9; no WINDOW layer
    ]:
        found = openings(ezdxf.readfile(rf"{root}\{name}.dxf").modelspace())
        got = (sum(k == "door" for k, *_ in found), sum(k == "window" for k, *_ in found))
        assert got == (doors, windows), (name, got, (doors, windows))
    print("ok")
