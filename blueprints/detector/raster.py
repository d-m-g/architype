"""Lane B. Raster floor plan (cubicasa5k PNG) -> Scene with doors, windows, fixtures, rooms and floors.

Calls the Detector deployed from train.py: uv run --extra detector modal deploy blueprints/detector/train.py
Trained only on plans where scoring.split_of(path) == "train".
"""
from pathlib import Path

from .. import costs
from ..schemas import Entity, Page, Scene

# detector class -> (entity type, subtype, label shown in the viewer)
KINDS = {
    "door": ("door", None, None),
    "window": ("window", None, None),
    "toilet": ("fixture", "toilet", None),
    "sink": ("fixture", "sink", None),
    "bathtub": ("fixture", "bathtub", None),
    "shower": ("fixture", "shower", None),
    "sauna_stove": ("room", "sauna", "sauna (stove)"),  # gold counts one sauna per stove: 90/91 plans
    "bedroom": ("room", "bedroom", None),
    "kitchen": ("room", "kitchen", None),
    "bathroom": ("room", "bathroom", None),
    "wc_room": ("room", "wc_room", None),
    "balcony": ("room", "balcony", None),
    "floor": ("level", None, None),
}
COVERS = sorted({f"{t}:{s}" if s else t for t, s, _ in KINDS.values()})
# Uploads: only what transfers beyond Finnish homes. Room types and floors stay uncovered, so those questions
# escalate instead of trusting a "bedroom" found in a school.
UPLOAD_CLASSES = ("door", "window", "toilet", "sink", "bathtub", "shower")
UPLOAD_COVERS = ["door", "window", "fixture:toilet", "fixture:sink", "fixture:bathtub", "fixture:shower"]
TILED = "fixtures-6:tiled@3"  # @3: a sheet whose doors came out small is run again at a larger scale (train.rescale)


def stale(scene: Scene) -> bool:
    """A cached upload scene from an older tiled detector: built again, so the upload gets today's detections."""
    head = scene.builder.split("+")[0]
    return head.startswith("fixtures-6:tiled@") and head != TILED


def build_scene(path: str, file: Path, upload: bool = False) -> Scene:
    """The live path: one call to the deployed Detector. Dataset plans are usually prebuilt into the scene cache
    (modal run blueprints/detector/train.py::prebuild), so this runs on a cache miss. upload=True tiles large
    sheets and covers only doors, windows and fixtures."""
    import modal

    detect = modal.Cls.from_name("blueprints-detector", "Detector")().detect
    out = detect.remote(file.read_bytes(), tile=upload)
    if upload:
        out["boxes"] = [b for b in out["boxes"] if b["cls"] in UPLOAD_CLASSES]
        scene = scene_from(path, out, covers=UPLOAD_COVERS, merge=False)
        if out["model"] == TILED.split("@")[0]:
            scene.builder = TILED
        return scene
    return scene_from(path, out)


def merge_windows(boxes: list[dict], width: int, height: int, gap_px: float = 0) -> list[dict]:
    """One window per run of collinear, touching window boxes. The model often splits a glazed band into its panes
    where the annotators drew one window; on val this lifts exact window counts from 42.1% to 47.8%."""
    wins = [b for b in boxes if b["cls"] == "window"]
    px = [(b["box"][0] * width, b["box"][1] * height, b["box"][2] * width, b["box"][3] * height) for b in wins]
    parent = list(range(len(wins)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(px):
        for j in range(i + 1, len(px)):
            b = px[j]
            horizontal = a[2] - a[0] > a[3] - a[1]
            if horizontal != (b[2] - b[0] > b[3] - b[1]):
                continue
            k = 1 if horizontal else 0  # axis across the wall
            thick = max(min(a[k + 2] - a[k], b[k + 2] - b[k]), 3)
            same_line = abs((a[k] + a[k + 2]) - (b[k] + b[k + 2])) / 2 < thick
            gap = max(a[1 - k], b[1 - k]) - min(a[3 - k], b[3 - k])
            if same_line and gap <= gap_px:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, b in enumerate(wins):
        groups.setdefault(find(i), []).append(b)
    merged = [{"cls": "window", "score": min(g["score"] for g in grp),
               "box": [min(g["box"][0] for g in grp), min(g["box"][1] for g in grp),
                       max(g["box"][2] for g in grp), max(g["box"][3] for g in grp)]} for grp in groups.values()]
    return [b for b in boxes if b["cls"] != "window"] + merged


def scene_from(path: str, out: dict, covers: list[str] | None = None, merge: bool = True) -> Scene:
    """Detector output -> Scene. Shared by the live path and the prebuild. merge: join touching window panes, a
    CubiCasa labelling habit; CAD sheets draw each window unit, so uploads keep them apart."""
    entities = []
    floors = 0
    boxes = merge_windows(out["boxes"], out["width"], out["height"]) if merge else out["boxes"]
    for i, b in enumerate(boxes):
        x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in b["box"])
        kind, subtype, label = KINDS[b["cls"]]
        if kind == "level":
            floors += 1
            label = f"floor {floors}"
        entities.append(Entity(
            id=f"det-{i}",
            type=kind,
            subtype=subtype,
            label=label,
            outdoor=b["cls"] == "balcony",
            geom=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            conf=b["score"],  # ponytail: raw detector score, isotonic calibration on val when confidence is judged
            ref=f"det:{i}",
        ))
    return Scene(
        path=path,
        builder=f"{out['model']}@2",
        tier="detect",
        covers=covers or COVERS,
        pages=[Page(image=path, width=out["width"], height=out["height"])],
        entities=entities,
        trace=[costs.gpu_step("scene", out["gpu"], out["seconds"])],
    )


if __name__ == "__main__":
    w = lambda x0, x1, y=0.5: {"cls": "window", "score": 0.9, "box": [x0, y, x1, y + 0.01]}
    # two touching panes in one wall -> one window; a separate window and a door stay as they are
    got = merge_windows([w(0.1, 0.2), w(0.2, 0.3), w(0.5, 0.6), {"cls": "door", "score": 0.9, "box": [0.1, 0.1, 0.2, 0.2]}], 1000, 1000)
    assert sorted(round(b["box"][0], 2) for b in got if b["cls"] == "window") == [0.1, 0.5], got
    assert sum(b["cls"] == "door" for b in got) == 1
    # an upload detected before rescaling is built again; today's, a single-pass upload and a CubiCasa scene are not
    sc = lambda b: Scene(path="uploads/x.png", builder=b, tier="detect", covers=[], pages=[])
    assert stale(sc("fixtures-6:tiled@2+vision:gemini-3.8-flash@5+outlines:rooms-1@4"))
    assert not any(stale(sc(b)) for b in (TILED + "+vision:g@5", "fixtures-6+all-13@2+vision:g@5", "fixtures-6+all-13@2"))
    # same x-range but different walls (rows) do not merge
    assert len(merge_windows([w(0.1, 0.2, 0.1), w(0.2, 0.3, 0.6)], 1000, 1000)) == 2
    print("ok")
