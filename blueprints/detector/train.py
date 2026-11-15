"""Lane B on Modal: fetch CubiCasa5K, convert its SVG labels to YOLO, train.

    uv run --extra detector modal run blueprints/detector/train.py::fetch
    uv run --extra detector modal run blueprints/detector/train.py::labels
    uv run --extra detector modal run --detach blueprints/detector/train.py::train

No leakage: plans with scoring.split_of(path) == "test" are never written to the training set, so neither
train nor val (used for early stopping and threshold tuning) ever sees them. CubiCasa plans that are not in
the dataset count as train. The SVGs are used for training only, never at question time (Stoa, 13 Nov).
"""
import modal

app = modal.App("blueprints-detector")
vol = modal.Volume.from_name("cubicasa5k", create_if_missing=True)
ZIP_URL = "https://zenodo.org/api/records/2613548/files/cubicasa5k.zip/content"  # CC BY-NC-SA 4.0
ZIP_MD5 = "0ce0b203d1e3c125b51087b219bd23b9"
RAW = "/vol/raw/cubicasa5k"
CHUNKS = "/vol/chunks"  # labels() writes the YOLO dataset here as one tar per worker; train/tune unpack them
CLASSES = ["door", "window", "toilet", "sink", "bathtub", "shower",  # 0-5: the fixtures-only model trains these
           "sauna_stove", "bedroom", "kitchen", "bathroom", "wc_room", "balcony", "floor",
           "doorway"]  # 13: open doorway without a leaf; trained so the model tells it from a door, never counted
ROOMS = 7  # first room class id
FIXTURES = "0,1,2,3,4,5,13"  # classes the fixtures models train on

cpu = modal.Image.debian_slim().apt_install("curl", "unzip")
conv = modal.Image.debian_slim().pip_install("svgelements", "pillow", "pydantic").add_local_python_source("blueprints")
gpu = (
    modal.Image.debian_slim()
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("ultralytics==8.3.40", "numpy<2")  # 8.3.40 still calls np.trapz, removed in numpy 2
    .add_local_python_source("blueprints")
)


def class_of(svg_class: str, label: str = "") -> int | None:
    """SVG group class (+ the room's drawn name) -> YOLO class id. Checked against the dataset's gold counts on
    135 plans: fixtures 643/644, bedrooms 85/85, kitchens 92/92, bathrooms 65/65, WC rooms 62/62, balconies 33/33,
    saunas (= sauna stoves) 90/91, floors 128/131."""
    words = svg_class.split()
    if not words:
        return None
    if words[0] == "Door":  # "Doors" is the container group; "Door None" is an open doorway without a leaf
        return 13 if "None" in words else 0
    if words[0] == "Window":
        return 1
    if svg_class.startswith("Floorplan Floor-"):
        return 12
    if words[0] == "Space" and len(words) > 1:
        if label.strip().upper().startswith("WC"):  # "WC", "WC/SH": the question's "separate WC rooms"
            return 10
        if words[1] == "Bath":  # KH, PH, KPH, PESUH, SH...
            return 9
        if words[1] == "Bedroom":
            return 7
        if words[1] == "Kitchen":  # incl. Kitchenette, Open, Scullery
            return 8
        if words[1:3] == ["Outdoor", "Balcony"]:  # incl. Glazed
            return 11
        return None
    if words[0] != "FixedFurniture" or len(words) < 2:
        return None
    if "SaunaStove" in svg_class:  # one stove per sauna
        return 6
    name = words[1]
    if name == "Toilet":
        return 2
    if "Sink" in name:  # Sink, RoundSink, DoubleSink(Right), SideSink, CornerSink: a double sink is one
        return 3
    if name in ("Bathtub", "Jacuzzi"):
        return 4
    if name in ("Shower", "ShowerPlatform"):  # ShowerCab/ShowerScreen are drawn around a Shower: not counted
        return 5
    return None


def svg_groups(path: str) -> list:
    """Every <g>. Floors 2-4 of a multi-floor plan sit in a <g class="Floor"> styled display:none, which svgelements
    skips, but the PNG shows them and the gold counts include them: unhide those groups only. Other hidden groups
    (FloorsCompose) hold shifted copies of the floors and must stay hidden."""
    import io
    import re

    from svgelements import SVG, Group

    def unhide(m):
        return m.group(0).replace("display: none;", "").replace("display:none;", "")

    text = re.sub(r'<g\b[^>]*\bclass="Floor"[^>]*>', unhide, open(path, encoding="utf-8").read())
    return list(SVG.parse(io.StringIO(text)).select(lambda e: isinstance(e, Group)))


def labelled_boxes(path: str) -> list[tuple[int, tuple]]:
    """(class id, (x0, y0, x1, y1) in SVG units) for every object we train on, deduplicated."""
    import xml.etree.ElementTree as ET

    from svgelements import Shape

    # Room names ("WC", "KH") by Space id. svgelements loses the text of some rooms; ElementTree does not.
    names = {
        e.get("id"): next((t.strip() for t in e.itertext() if t.strip()), "")
        for e in ET.parse(path).getroot().iter("{http://www.w3.org/2000/svg}g")
        if (e.get("class") or "").startswith("Space ")
    }
    out, seen = [], set()
    for g in svg_groups(path):
        svg_class = g.values.get("class", "")
        cls = class_of(svg_class, names.get(g.id, "") if svg_class.startswith("Space ") else "")
        if cls is None:
            continue
        shapes = [s for s in g.select() if isinstance(s, Shape)]
        if ROOMS <= cls < 12:
            shapes = shapes[:1]  # the room outline; the name and dimension labels can stick out of the room
        boxes = []
        for s in shapes:
            try:
                b = s.bbox()
            except ValueError:  # e.g. text sized in em units
                continue
            if b is not None:
                boxes.append(b)
        if not boxes:
            continue
        box = tuple(round(float(f(b[i] for b in boxes)), 1) for f, i in ((min, 0), (min, 1), (max, 2), (max, 3)))
        if (cls, box) not in seen:  # svgelements yields some groups twice once hidden floors are unhidden
            seen.add((cls, box))
            out.append((cls, box))
    return out


@app.function(image=cpu, volumes={"/vol": vol}, timeout=3600, cpu=4)
def fetch():
    """Download the public archive into the volume once; keeps only model.svg and the PNGs."""
    import hashlib
    import os
    import subprocess

    if os.path.exists("/vol/raw/.done"):
        print("already fetched")
        return
    subprocess.run(["curl", "-L", "--retry", "5", "-o", "/tmp/c.zip", ZIP_URL], check=True)
    md5 = hashlib.md5()
    with open("/tmp/c.zip", "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            md5.update(chunk)
    assert md5.hexdigest() == ZIP_MD5, md5.hexdigest()
    subprocess.run(["unzip", "-q", "/tmp/c.zip", "*.svg", "*.png", "*.txt", "-d", "/vol/raw"], check=True)
    open("/vol/raw/.done", "w").close()
    vol.commit()


@app.function(image=conv, volumes={"/vol": vol}, timeout=3600)
def convert(folders: list[str], chunk: int) -> list[tuple[str, str]]:
    """F1_original.png + YOLO labels for each train plan, as one tar in CHUNKS. Returns (stem, split) per plan.
    Built on local disk and written as one file: reading thousands of small files back off the volume took 25 min."""
    import hashlib
    import os
    import shutil

    YOLO_DIR = "/tmp/yolo"

    from PIL import Image

    from blueprints.scoring import split_of

    done = []
    for folder in folders:  # "high_quality/9928"
        stem = folder.replace("/", "-")
        if split_of(f"data/cubicasa5k/{stem}.png") == "test":
            continue  # the shared test set: never trained, validated or tuned on
        split = "val" if int(hashlib.md5(stem.encode()).hexdigest(), 16) % 10 == 0 else "train"
        src = f"{RAW}/{folder}"
        w, h = Image.open(f"{src}/F1_original.png").size
        sw, sh = Image.open(f"{src}/F1_scaled.png").size  # SVG coordinates are in F1_scaled pixels
        kx, ky = w / sw, h / sh
        try:
            objects = labelled_boxes(f"{src}/model.svg")
        except Exception as e:  # a few SVGs have malformed attributes: drop the plan, not the run
            print(f"skip {folder}: {e!r}")
            continue
        lines = []
        for cls, box in objects:
            x0, y0, x1, y1 = box[0] * kx, box[1] * ky, box[2] * kx, box[3] * ky
            x0, x1 = max(0, x0), min(w, x1)
            y0, y1 = max(0, y0), min(h, y1)
            bw, bh = max(x1 - x0, 4), max(y1 - y0, 4)  # windows are thin; keep at least 4 px
            if x1 <= x0 or y1 <= y0:
                continue
            lines.append(f"{cls} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {min(bw / w, 1):.6f} {min(bh / h, 1):.6f}")
        os.makedirs(f"{YOLO_DIR}/images/{split}", exist_ok=True)
        os.makedirs(f"{YOLO_DIR}/labels/{split}", exist_ok=True)
        shutil.copyfile(f"{src}/F1_original.png", f"{YOLO_DIR}/images/{split}/{stem}.png")
        with open(f"{YOLO_DIR}/labels/{split}/{stem}.txt", "w") as f:
            f.write("\n".join(lines))
        done.append((stem, split))
    os.makedirs(CHUNKS, exist_ok=True)
    if done:
        shutil.make_archive(f"{CHUNKS}/{chunk:03d}", "tar", YOLO_DIR)
    vol.commit()
    return done


@app.function(image=conv, volumes={"/vol": vol}, timeout=3600)
def labels():
    import glob
    import shutil

    vol.reload()
    shutil.rmtree(CHUNKS, ignore_errors=True)
    vol.commit()
    folders = sorted(p[len(RAW) + 1 : -len("/model.svg")] for p in glob.glob(f"{RAW}/*/*/model.svg"))
    chunks = [folders[i : i + 100] for i in range(0, len(folders), 100)]
    done = []
    for i, part in enumerate(convert.map(chunks, range(len(chunks))), 1):
        done += part
        print(f"converted {i}/{len(chunks)} chunks, {len(done)} plans written", flush=True)
    n_val = sum(s == "val" for _, s in done)
    print(f"{len(folders)} plans, {len(done) - n_val} train, {n_val} val, {len(folders) - len(done)} test skipped")


def unpack_dataset() -> str:
    """The YOLO dataset on local disk (the dataloader is much faster there than on the volume)."""
    import glob
    import shutil

    vol.reload()
    for t in sorted(glob.glob(f"{CHUNKS}/*.tar")):
        shutil.unpack_archive(t, "/tmp/yolo")
    open("/tmp/yolo/data.yaml", "w").write(f"path: /tmp/yolo\ntrain: images/train\nval: images/val\nnames: {CLASSES}\n")
    return "/tmp/yolo"


@app.function(image=gpu, volumes={"/vol": vol}, gpu="L40S", timeout=6 * 3600, cpu=8, memory=32768)
def train(name: str, classes: str = FIXTURES, model: str = "yolo11s.pt", epochs: int = 80, imgsz: int = 1024,
          batch: int = 16):
    """classes: comma-separated class ids to train on (the others are filtered out of the same labels)."""
    import os

    from ultralytics import YOLO

    root = unpack_dataset()
    print({s: len(os.listdir(f"{root}/images/{s}")) for s in ("train", "val")}, flush=True)
    YOLO(model).train(
        data=f"{root}/data.yaml", imgsz=imgsz, epochs=epochs, batch=batch, workers=8, patience=20,
        fliplr=0.5, flipud=0.5,  # both together give the 180° rotation seen in the dataset
        degrees=0, scale=0.5, mosaic=1.0, close_mosaic=10,
        classes=[int(c) for c in classes.split(",")],
        project="/vol/runs", name=name, exist_ok=True,
    )
    vol.commit()


@app.function(image=gpu, volumes={"/vol": vol}, gpu="L40S", timeout=3600)
def tune(name: str, classes: str = FIXTURES, imgsz: int = 1024):
    """Per-class score threshold that maximises exact-count accuracy on val (a slice of train: no test plans)."""
    import glob
    import json
    from collections import Counter

    from ultralytics import YOLO

    m = YOLO(f"/vol/runs/{name}/weights/best.pt")
    imgs = sorted(glob.glob(f"{unpack_dataset()}/images/val/*.png"))
    scores, gold = [], []
    for i in range(0, len(imgs), 32):
        for r in m.predict(imgs[i : i + 32], imgsz=imgsz, conf=0.05, verbose=False):
            scores.append([(int(c), float(s)) for c, s in zip(r.boxes.cls, r.boxes.conf)])
            txt = r.path.replace("/images/", "/labels/").replace(".png", ".txt")
            gold.append(Counter(int(l.split()[0]) for l in open(txt).read().splitlines() if l))
    best = {}
    for k in (int(c) for c in classes.split(",")):
        cls = CLASSES[k]
        acc = {
            t / 100: sum(sum(c == k and s >= t / 100 for c, s in sc) == g[k] for sc, g in zip(scores, gold)) / len(gold)
            for t in range(5, 96)
        }
        t = max(acc, key=acc.get)
        best[cls] = t
        print(f"{cls:12s} threshold {t:.2f}  exact count {acc[t]:.1%} on {len(gold)} val plans")
    json.dump(best, open(f"/vol/runs/{name}/thresholds.json", "w"))
    vol.commit()


# The run each class is served from. On val, fixtures-6 counts windows 4 points better; all-13 is the only one
# with rooms, saunas and floors. Fixture counts are otherwise within noise of each other.
SERVE = {"fixtures-6": (CLASSES[:ROOMS - 1], 1024), "all-13": (CLASSES[ROOMS - 1:13], 1024)}  # run: (classes, imgsz)

# Tiled inference for uploads. A first pass at TILE_LONG_SIDE probes the drawing's scale: when its doors come out
# under DOOR_MIN_PX (a 7,000 px dormitory sheet: 16 px, and armchairs taken for doors), the sheet is run again so the
# median door is DOOR_PX, never above the full resolution. A 14,000 px school sheet keeps the first pass.
TILE, TILE_OVERLAP, TILE_LONG_SIDE = 1024, 256, 3072
DOOR_PX, DOOR_MIN_PX = 40, 30  # CubiCasa doors are ~30-50 px at the size trained on


def rescale(s: float, door_sides: list[float]) -> float | None:
    """The scale for a second pass, or None when the first one (at s) already saw doors big enough, or saw too few
    doors to tell. door_sides: long sides in pixels at scale s."""
    import statistics

    if len(door_sides) < 3:
        return None
    d = statistics.median(door_sides)
    return min(1.0, s * DOOR_PX / d) if d < DOOR_MIN_PX and s < 1.0 else None


def merge_tiles(boxes: list[dict], iou: float = 0.5, inside: float = 0.6) -> list[dict]:
    """Per class, keep the best box and drop any box that overlaps it (IoU) or lies mostly inside it: the same
    door seen whole in one tile and cut off at the edge of the next."""
    kept: list[dict] = []
    for b in sorted(boxes, key=lambda b: -b["score"]):
        x0, y0, x1, y1 = b["box"]
        area = max((x1 - x0) * (y1 - y0), 1e-9)
        dup = False
        for k in kept:
            if k["cls"] != b["cls"]:
                continue
            kx0, ky0, kx1, ky1 = k["box"]
            inter = max(0, min(x1, kx1) - max(x0, kx0)) * max(0, min(y1, ky1) - max(y0, ky0))
            karea = (kx1 - kx0) * (ky1 - ky0)
            if inter / (area + karea - inter) > iou or inter / area > inside or inter / max(karea, 1e-9) > inside:
                dup = True
                break
        if not dup:
            kept.append(b)
    return kept


# A cold start is ~3.5 min (image pull + two models); warm calls take seconds. For a live demo deploy with
# DETECTOR_WARM=1 to keep one T4 up (~$0.59/h), and redeploy without it afterwards.
@app.cls(image=gpu, volumes={"/vol": vol}, gpu="T4", scaledown_window=300,
         min_containers=int(__import__("os").environ.get("DETECTOR_WARM", "0")))
class Detector:
    @modal.enter()
    def load(self):
        import json

        from ultralytics import YOLO

        self.models = {run: YOLO(f"/vol/runs/{run}/weights/best.pt") for run in SERVE}
        self.thresholds = {run: json.load(open(f"/vol/runs/{run}/thresholds.json")) for run in SERVE}

    @modal.method()
    def detect(self, png: bytes, tile: bool = False) -> dict:
        """Boxes above each class's tuned threshold, normalised 0..1, plus the GPU seconds spent.
        tile=True for uploads: large sheets are scaled and cut into tiles so symbols reach the size trained on."""
        import io

        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 300_000_000  # an A1 scan at 600 dpi is ~250 MP; above that, refuse
        im = Image.open(io.BytesIO(png))
        return self._tiled(im) if tile else self._run(im)

    def _tiled(self, im) -> dict:
        """Fixtures model only (the room classes are Finnish-residential). Scale the long side to TILE_LONG_SIDE,
        run 1024 px tiles with overlap, drop blank tiles, merge duplicates across tile edges; again at a larger
        scale when that left the doors too small (rescale)."""
        import time

        t = time.perf_counter()
        im = im.convert("RGB")
        W, H = im.size
        s = min(1.0, TILE_LONG_SIDE / max(W, H))
        if max(W, H) * s <= 1280:  # small enough for one pass, as on CubiCasa
            out = self._run(im, runs=["fixtures-6"])
            out["seconds"] = time.perf_counter() - t
            return out
        boxes, n = self._tiles(im, s)
        doors = [max((b["box"][2] - b["box"][0]) * W, (b["box"][3] - b["box"][1]) * H) * s for b in boxes if b["cls"] == "door"]
        s2 = rescale(s, doors)
        if s2:
            boxes, n2 = self._tiles(im, s2)
            n += n2
        return {"model": "fixtures-6:tiled", "gpu": "T4", "seconds": time.perf_counter() - t, "width": W,
                "height": H, "boxes": boxes, "tiles": n, "scale": round(s2 or s, 3)}

    def _tiles(self, im, s: float) -> tuple[list[dict], int]:
        """Boxes (0..1) of the sheet scaled by s and cut into overlapping tiles, and the number of tiles run."""
        import numpy as np

        W, H = im.size
        small = im.resize((round(W * s), round(H * s)), resample=1) if s < 1.0 else im  # LANCZOS
        w, h = small.size
        step = TILE - TILE_OVERLAP
        xs = sorted({*range(0, max(w - TILE, 0) + 1, step), max(w - TILE, 0)})
        ys = sorted({*range(0, max(h - TILE, 0) + 1, step), max(h - TILE, 0)})
        tiles, offsets = [], []
        for y in ys:
            for x in xs:
                crop = small.crop((x, y, x + TILE, y + TILE))
                if np.asarray(crop.convert("L")).min() < 200:  # skip blank paper
                    tiles.append(crop)
                    offsets.append((x, y))
        classes, imgsz = SERVE["fixtures-6"]
        th = {c: self.thresholds["fixtures-6"][c] for c in classes}
        found = []
        for i in range(0, len(tiles), 16):
            results = self.models["fixtures-6"].predict(tiles[i:i + 16], imgsz=imgsz, conf=min(th.values()),
                                                        classes=[CLASSES.index(c) for c in classes], verbose=False)
            for (ox, oy), r in zip(offsets[i:i + 16], results):
                for (x0, y0, x1, y1), c, sc in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
                    cls = CLASSES[int(c)]
                    if sc >= th[cls]:
                        found.append({"cls": cls, "score": sc, "box": [x0 + ox, y0 + oy, x1 + ox, y1 + oy]})
        boxes = []
        for b in merge_tiles(found):
            x0, y0, x1, y1 = b["box"]
            boxes.append({**b, "box": [x0 / w, y0 / h, x1 / w, y1 / h]})
        return boxes, len(tiles)

    @modal.method()
    def detect_raw(self, folder: str) -> dict:
        """The same on a CubiCasa plan in the volume ("high_quality/9928"), byte-identical to the dataset PNG
        (checked for all 4,787): the prebuild sends a name instead of uploading 1.7 GB."""
        from PIL import Image

        return self._run(Image.open(f"{RAW}/{folder}/F1_original.png"))

    def _run(self, im, runs: list[str] | None = None) -> dict:
        import time

        t = time.perf_counter()
        im = im.convert("RGB")
        boxes = []
        for run, (classes, imgsz) in SERVE.items():
            if runs and run not in runs:
                continue
            th = {c: self.thresholds[run][c] for c in classes}
            ids = [CLASSES.index(c) for c in classes]
            r = self.models[run].predict(im, imgsz=imgsz, conf=min(th.values()), classes=ids, verbose=False)[0]
            for (x0, y0, x1, y1), c, s in zip(r.boxes.xyxyn.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
                cls = CLASSES[int(c)]
                if s >= th[cls]:
                    boxes.append({"cls": cls, "score": s, "box": [x0, y0, x1, y1]})
        return {"model": "+".join(SERVE), "gpu": "T4", "seconds": time.perf_counter() - t, "width": im.width,
                "height": im.height, "boxes": boxes}


@app.local_entrypoint()
def prebuild(limit: int = 0):
    """Scenes for every cubicasa plan in questions.jsonl into the local scene cache, so the demo answers without a
    GPU call. Inference only: the model never trained on test plans. Rerun after retraining.

        uv run --extra detector modal run blueprints/detector/train.py::prebuild
    """
    import hashlib
    import json

    from blueprints import pipeline
    from blueprints.detector.raster import scene_from

    paths = sorted({json.loads(l)["path"] for l in open(pipeline.DATA_ROOT / "questions.jsonl") if "cubicasa5k" in l})
    paths = paths[:limit] if limit else paths
    folders = [p.split("/")[-1][:-4].rsplit("-", 1) for p in paths]  # "high_quality-9928" -> folder/number
    out_dir = pipeline.CACHE / "scenes" / "cubicasa5k"
    out_dir.mkdir(parents=True, exist_ok=True)
    det = Detector()
    for i, (path, out) in enumerate(zip(paths, det.detect_raw.map([f"{a}/{b}" for a, b in folders])), 1):
        scene = scene_from(path, out)
        (out_dir / (hashlib.md5(path.encode()).hexdigest() + ".json")).write_text(scene.model_dump_json())
        if i % 250 == 0 or i == len(paths):
            print(f"{i}/{len(paths)} scenes written", flush=True)


if __name__ == "__main__":
    assert class_of("Door Swing Beside") == 0 and class_of("Door None Beside") == 13 and class_of("Doors") is None
    assert class_of("Window Regular") == 1 and class_of("FixedFurniture DoubleSinkRight") == 3
    assert class_of("FixedFurniture ShowerCab") is None and class_of("FixedFurniture ShowerScreen") is None
    assert class_of("FixedFurniture Jacuzzi") == 4 and class_of("FixedFurniture Shower") == 5
    assert class_of("FixedFurniture ElectricalAppliance SaunaStove Round") == 6
    assert class_of("Space Bath", "WC") == 10 and class_of("Space Bath Shower", "PH") == 9
    assert class_of("Space UserDefined", "WC/SH") == 10 and class_of("Space UserDefined", "VAR") is None
    assert class_of("Space Bedroom", "MH") == 7 and class_of("Space Kitchen Kitchenette", "KK") == 8
    assert class_of("Space Outdoor Balcony Glazed", "PARVEKE") == 11 and class_of("Space Outdoor Terrace") is None
    assert class_of("Floorplan Floor-2") == 12 and class_of("Floor") is None
    door = lambda x0, x1, s=0.9: {"cls": "door", "score": s, "box": [x0, 0, x1, 10]}
    # the same door whole in one tile and cut in half in the next -> one; a door further along stays
    assert len(merge_tiles([door(0, 10), door(5, 10, 0.6), door(50, 60)])) == 2
    assert len(merge_tiles([door(0, 10), {"cls": "window", "score": 0.5, "box": [0, 0, 10, 10]}])) == 2
    # dormitory sheet: doors 16 px at 0.437 -> full resolution; school sheet: doors big enough -> no second pass
    assert rescale(0.437, [16, 15, 17]) == 1.0 and rescale(0.2, [10, 10, 10]) == 0.8
    assert rescale(0.22, [40, 45, 38]) is None and rescale(0.437, [16, 16]) is None and rescale(1.0, [10, 10, 10]) is None
    print("ok")
