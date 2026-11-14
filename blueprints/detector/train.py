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
YOLO_DIR = "/vol/yolo"
CLASSES = ["door", "window", "toilet", "sink", "bathtub", "shower",  # 0-5: the fixtures-only model trains these
           "sauna_stove", "bedroom", "kitchen", "bathroom", "wc_room", "balcony", "floor"]
ROOMS = 7  # first room class id

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
        return None if "None" in words else 0
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
def convert(folders: list[str]) -> list[tuple[str, str]]:
    """Write F1_original.png + YOLO labels for each train plan. Returns (stem, split) for what was written."""
    import hashlib
    import os
    import shutil

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
    vol.commit()
    return done


@app.function(image=conv, volumes={"/vol": vol}, timeout=3600)
def labels():
    import glob
    import os
    import shutil

    vol.reload()
    shutil.rmtree(YOLO_DIR, ignore_errors=True)
    if os.path.exists("/vol/yolo.tar"):
        os.remove("/vol/yolo.tar")  # train's cached copy of the old labels
    folders = sorted(p[len(RAW) + 1 : -len("/model.svg")] for p in glob.glob(f"{RAW}/*/*/model.svg"))
    chunks = [folders[i : i + 100] for i in range(0, len(folders), 100)]
    done = []
    for i, part in enumerate(convert.map(chunks), 1):
        done += part
        print(f"converted {i}/{len(chunks)} chunks, {len(done)} plans written", flush=True)
    n_val = sum(s == "val" for _, s in done)
    print(f"{len(folders)} plans, {len(done) - n_val} train, {n_val} val, {len(folders) - len(done)} test skipped")
    vol.reload()  # see the workers' files
    shutil.make_archive("/vol/yolo", "tar", YOLO_DIR)  # one file: train unpacks it in seconds, not 9 min
    vol.commit()
    print("tar written", flush=True)


@app.function(image=gpu, volumes={"/vol": vol}, gpu="L40S", timeout=6 * 3600, cpu=8, memory=32768)
def train(name: str = "all-13", n_classes: int = 13, model: str = "yolo11s.pt", epochs: int = 80, imgsz: int = 1024):
    """n_classes=6 trains the fixtures-only model on the same labels (the other classes are filtered out)."""
    import os
    import shutil

    from ultralytics import YOLO

    # Local disk: the dataloader is much faster than on the volume. labels() writes the tar.
    shutil.unpack_archive("/vol/yolo.tar", "/tmp/yolo")
    open("/tmp/yolo/data.yaml", "w").write(f"path: /tmp/yolo\ntrain: images/train\nval: images/val\nnames: {CLASSES}\n")
    print({s: len(os.listdir(f"/tmp/yolo/images/{s}")) for s in ("train", "val")}, flush=True)
    YOLO(model).train(
        data="/tmp/yolo/data.yaml", imgsz=imgsz, epochs=epochs, batch=16, workers=8, patience=20,
        fliplr=0.5, flipud=0.5,  # both together give the 180° rotation seen in the dataset
        degrees=0, scale=0.5, mosaic=1.0, close_mosaic=10,
        classes=list(range(n_classes)) if n_classes < len(CLASSES) else None,
        project="/vol/runs", name=name, exist_ok=True,
    )
    vol.commit()


@app.function(image=gpu, volumes={"/vol": vol}, gpu="L40S", timeout=3600)
def tune(name: str = "all-13", n_classes: int = 13, imgsz: int = 1024):
    """Per-class score threshold that maximises exact-count accuracy on val (a slice of train: no test plans)."""
    import glob
    import json
    from collections import Counter

    from ultralytics import YOLO

    vol.reload()
    m = YOLO(f"/vol/runs/{name}/weights/best.pt")
    imgs = sorted(glob.glob(f"{YOLO_DIR}/images/val/*.png"))
    scores, gold = [], []
    for i in range(0, len(imgs), 32):
        for r in m.predict(imgs[i : i + 32], imgsz=imgsz, conf=0.05, verbose=False):
            scores.append([(int(c), float(s)) for c, s in zip(r.boxes.cls, r.boxes.conf)])
            txt = r.path.replace("/images/", "/labels/").replace(".png", ".txt")
            gold.append(Counter(int(l.split()[0]) for l in open(txt).read().splitlines() if l))
    best = {}
    for k, cls in enumerate(CLASSES[:n_classes]):
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
SERVE = {"fixtures-6": CLASSES[:ROOMS - 1], "all-13": CLASSES[ROOMS - 1:]}


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
    def detect(self, png: bytes) -> dict:
        """Boxes above each class's tuned threshold, normalised 0..1, plus the GPU seconds spent."""
        import io

        from PIL import Image

        return self._run(Image.open(io.BytesIO(png)))

    @modal.method()
    def detect_raw(self, folder: str) -> dict:
        """The same on a CubiCasa plan in the volume ("high_quality/9928"), byte-identical to the dataset PNG
        (checked for all 4,787): the prebuild sends a name instead of uploading 1.7 GB."""
        from PIL import Image

        return self._run(Image.open(f"{RAW}/{folder}/F1_original.png"))

    def _run(self, im) -> dict:
        import time

        t = time.perf_counter()
        im = im.convert("RGB")
        boxes = []
        for run, classes in SERVE.items():
            th = {c: self.thresholds[run][c] for c in classes}
            ids = [CLASSES.index(c) for c in classes]
            r = self.models[run].predict(im, imgsz=1024, conf=min(th.values()), classes=ids, verbose=False)[0]
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
    assert class_of("Door Swing Beside") == 0 and class_of("Door None Beside") is None and class_of("Doors") is None
    assert class_of("Window Regular") == 1 and class_of("FixedFurniture DoubleSinkRight") == 3
    assert class_of("FixedFurniture ShowerCab") is None and class_of("FixedFurniture ShowerScreen") is None
    assert class_of("FixedFurniture Jacuzzi") == 4 and class_of("FixedFurniture Shower") == 5
    assert class_of("FixedFurniture ElectricalAppliance SaunaStove Round") == 6
    assert class_of("Space Bath", "WC") == 10 and class_of("Space Bath Shower", "PH") == 9
    assert class_of("Space UserDefined", "WC/SH") == 10 and class_of("Space UserDefined", "VAR") is None
    assert class_of("Space Bedroom", "MH") == 7 and class_of("Space Kitchen Kitchenette", "KK") == 8
    assert class_of("Space Outdoor Balcony Glazed", "PARVEKE") == 11 and class_of("Space Outdoor Terrace") is None
    assert class_of("Floorplan Floor-2") == 12 and class_of("Floor") is None
    print("ok")
