"""Doors and windows from a Swiss Dwellings DXF modelspace.

Each door or window is drawn around one rectangle polyline on its layer: a door is a box (the frame)
plus its swing arc(s) and leaf line(s), a window is a closed box plus its glazing lines. Gold counts
one per box, so a double door (one box, two arcs) is one door, and a box drawn twice
(identical geometry, new handle; most plans have some) counts twice. Counting boxes matched gold on
every train plan; counting arcs or touching groups did not.
"""


def openings(msp) -> list[tuple[str, list[tuple[float, float]], str]]:
    out = []
    for kind, layer in (("door", "DOOR"), ("window", "WINDOW")):
        for e in msp.query(f'LWPOLYLINE[layer=="{layer}"]'):
            pts = [(float(x), float(y)) for x, y, *_ in e.get_points()]
            # Window boxes are closed 4-vertex polylines; door boxes are open 5-vertex ones that end where they start.
            if e.closed and len(pts) == 4 or len(pts) == 5 and pts[0] == pts[-1]:
                out.append((kind, pts[:4], f"dxf:{e.dxf.handle}"))
    return out


if __name__ == "__main__":
    import ezdxf

    root = r"C:\Users\pradh\Downloads\junction-dataset\junction-dataset\data\swiss_dwellings"
    for name, doors, windows in [
        ("site3387_bldg5238_floor23498", 20, 11),  # 4 double doors, 2 duplicated doors
        ("site1601_bldg2794_floor12912", 18, 15),
        ("site70_bldg21_floor88", 26, 14),
        ("site3912_bldg6120_floor25531", 0, 1),  # no DOOR layer
        ("site613_bldg1328_floor11989", 9, 0),  # no WINDOW layer
    ]:
        found = openings(ezdxf.readfile(rf"{root}\{name}.dxf").modelspace())
        got = (sum(k == "door" for k, *_ in found), sum(k == "window" for k, *_ in found))
        assert got == (doors, windows), (name, got, (doors, windows))
    print("ok")
