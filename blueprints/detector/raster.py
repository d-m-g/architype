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


def build_scene(path: str, file: Path) -> Scene:
    """The live path: one call to the deployed Detector. Dataset plans are usually prebuilt into the scene cache
    (modal run blueprints/detector/train.py::prebuild), so this runs on a cache miss."""
    import modal

    detect = modal.Cls.from_name("blueprints-detector", "Detector")().detect
    return scene_from(path, detect.remote(file.read_bytes()))


def scene_from(path: str, out: dict) -> Scene:
    """Detector output -> Scene. Shared by the live path and the prebuild."""
    entities = []
    floors = 0
    for i, b in enumerate(out["boxes"]):
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
        covers=COVERS,
        pages=[Page(image=path, width=out["width"], height=out["height"])],
        entities=entities,
        trace=[costs.gpu_step("scene", out["gpu"], out["seconds"])],
    )
