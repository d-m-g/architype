"""Lane 4. Room outlines on rasters: a YOLO segmentation model trained on CubiCasa5K room polygons and on the Swiss
plans (PNG crop + the DXF's ROOM_OUTLINE). Its own Modal app, so the live blueprints-detector is never touched; it
shares that app's volume (read /vol/raw, write only under /vol/seg) and images.

    uv run --extra detector modal run -m blueprints.detector.segment::labels          CubiCasa polygons -> /vol/seg/chunks
    uv run python -m blueprints.detector.segment swiss /tmp/swiss.tar                 Swiss labels, built locally (the DXFs are not on Modal)
    uv run --extra detector modal volume put cubicasa5k /tmp/swiss.tar seg/chunks/swiss.tar
    uv run --extra detector modal run --detach -m blueprints.detector.segment::train
    uv run --extra detector modal run -m blueprints.detector.segment::evaluate
    uv run --extra detector modal deploy -m blueprints.detector.segment

No leakage: plans with scoring.split_of(path) == "test" never reach train or val. CubiCasa test plans are not written
at all; Swiss test plans go to a third split, images/test, that only evaluate() reads.
"""
import modal

from blueprints.detector import train as det  # the volume, the images and the SVG reading; its app is not ours

app = modal.App("blueprints-rooms")
vol = det.vol
CHUNKS, RUNS = "/vol/seg/chunks", "/vol/seg/runs"
RUN = "rooms-1"  # the run Segmenter serves
# The schema's room subtypes, so a mask carries the room type.
NAMES = ["bedroom", "kitchen", "bathroom", "wc_room", "sauna", "balcony", "living", "corridor", "storage", "other"]
BY_WORD = {"Sauna": "sauna", "LivingRoom": "living", "Lounge": "living", "Entry": "corridor", "Hall": "corridor",
           "HallWay": "corridor", "DraughtLobby": "corridor", "Storage": "storage", "Closet": "storage",
           "Pantry": "storage", "DressingRoom": "storage"}


def room_class(svg_class: str, label: str = "") -> int | None:
    """SVG Space class (+ the room's drawn name) -> index into NAMES. The five types the detector also finds are told
    apart exactly as it does (train.class_of), so a mask and a detector box agree on what a WC room is."""
    words = svg_class.split()
    if len(words) < 2 or words[0] != "Space":
        return None
    k = det.class_of(svg_class, label)
    return NAMES.index(det.CLASSES[k] if k is not None else BY_WORD.get(words[1], "other"))


def room_polygons(path: str) -> list[tuple[int, tuple]]:
    """(class, outline points in SVG units) for every room of a CubiCasa model.svg, deduplicated."""
    import xml.etree.ElementTree as ET

    from svgelements import Path, Shape

    names = {e.get("id"): next((t.strip() for t in e.itertext() if t.strip()), "")
             for e in ET.parse(path).getroot().iter("{http://www.w3.org/2000/svg}g")
             if (e.get("class") or "").startswith("Space ")}
    out, seen = [], set()
    for g in det.svg_groups(path):
        svg_class = g.values.get("class", "")
        cls = room_class(svg_class, names.get(g.id, ""))
        shape = next((s for s in g.select() if isinstance(s, Shape)), None)  # the outline; the rest is its label
        if cls is None or shape is None:
            continue
        pts = tuple(dict.fromkeys((round(seg.end.x, 1), round(seg.end.y, 1)) for seg in abs(Path(shape)) if seg.end is not None))
        if len(pts) >= 3 and pts not in seen:
            seen.add(pts)
            out.append((cls, pts))
    return out


def label_line(cls: int, pts) -> str:
    """One YOLO segmentation label: class and the polygon, 0..1 of the image."""
    return f"{cls} " + " ".join(f"{min(max(x, 0), 1):.5f} {min(max(y, 0), 1):.5f}" for x, y in pts)


def val_or_train(stem: str) -> str:
    import hashlib
    return "val" if int(hashlib.md5(stem.encode()).hexdigest(), 16) % 10 == 0 else "train"  # as train.convert


@app.function(image=det.conv, volumes={"/vol": vol}, timeout=3600)
def convert(folders: list[str], chunk: int) -> list[tuple[str, str]]:
    """F1_original.png + polygon labels for each non-test CubiCasa plan, as one tar in CHUNKS (see train.convert)."""
    import os
    import shutil

    from PIL import Image

    from blueprints.scoring import split_of

    root, done = "/tmp/seg", []
    for folder in folders:
        stem = folder.replace("/", "-")
        if split_of(f"data/cubicasa5k/{stem}.png") == "test":
            continue
        split, src = val_or_train(stem), f"{det.RAW}/{folder}"
        w, h = Image.open(f"{src}/F1_original.png").size
        sw, sh = Image.open(f"{src}/F1_scaled.png").size  # SVG coordinates are in F1_scaled pixels
        try:
            rooms = room_polygons(f"{src}/model.svg")
        except Exception as e:
            print(f"skip {folder}: {e!r}")
            continue
        os.makedirs(f"{root}/images/{split}", exist_ok=True)
        os.makedirs(f"{root}/labels/{split}", exist_ok=True)
        shutil.copyfile(f"{src}/F1_original.png", f"{root}/images/{split}/{stem}.png")
        with open(f"{root}/labels/{split}/{stem}.txt", "w") as f:
            f.write("\n".join(label_line(cls, [(x / sw, y / sh) for x, y in pts]) for cls, pts in rooms))
        done.append((stem, split))
    os.makedirs(CHUNKS, exist_ok=True)
    if done:
        shutil.make_archive(f"{CHUNKS}/{chunk:03d}", "tar", root)
    vol.commit()
    return done


@app.function(image=det.conv, volumes={"/vol": vol}, timeout=3600)
def labels():
    import glob
    import os

    vol.reload()
    for old in glob.glob(f"{CHUNKS}/[0-9]*.tar"):  # swiss.tar, uploaded from a laptop, stays
        os.remove(old)
    vol.commit()
    folders = sorted(p[len(det.RAW) + 1: -len("/model.svg")] for p in glob.glob(f"{det.RAW}/*/*/model.svg"))
    chunks = [folders[i: i + 100] for i in range(0, len(folders), 100)]
    done = [d for part in convert.map(chunks, range(len(chunks))) for d in part]
    n_val = sum(s == "val" for _, s in done)
    print(f"{len(folders)} plans, {len(done) - n_val} train, {n_val} val, {len(folders) - len(done)} test or unreadable, skipped")


def _swiss_plan(args) -> tuple[str, str, int] | None:
    """One Swiss plan -> a PNG cropped to its rooms, polygon labels and its scale, under root. (stem, split, rooms)."""
    import json
    import statistics
    from pathlib import Path

    from PIL import Image
    from shapely.geometry import Polygon

    from blueprints.parsers import swiss
    from blueprints.scoring import split_of

    pdf, root, types = args
    pdf = Path(pdf)
    path = f"data/swiss_dwellings/{pdf.name}"
    try:
        rooms = [e for e in swiss.build_scene(path, pdf).entities if e.type == "room" and len(e.geom) >= 3]
    except Exception as e:
        print(f"skip {pdf.name}: {e!r}")
        return None
    if not rooms:
        return None
    stem = f"swiss-{pdf.stem}"
    split = "test" if split_of(path) == "test" else val_or_train(stem)
    im = Image.open(pdf.with_suffix(".png")).convert("RGB")
    W, H = im.size
    xs, ys = [x * W for r in rooms for x, _ in r.geom], [y * H for r in rooms for _, y in r.geom]
    pad = 0.04 * max(W, H)  # the sheet is mostly legend and title block: keep the plan
    x0, y0, x1, y1 = (round(v) for v in (max(min(xs) - pad, 0), max(min(ys) - pad, 0), min(max(xs) + pad, W), min(max(ys) + pad, H)))
    w, h = x1 - x0, y1 - y0
    lines, scales = [], []
    for r in rooms:
        # ponytail: the exterior only. A room around a shaft keeps the shaft's area; YOLO polygons have no holes.
        name = "balcony" if r.outdoor else types.get(" ".join((r.label or "").split()).casefold(), "other")
        lines.append(label_line(NAMES.index(name if name in NAMES else "other"), [((x * W - x0) / w, (y * H - y0) / h) for x, y in r.geom]))
        px = Polygon([(x * W, y * H) for x, y in r.geom]).area
        if r.qty.get("m2") and px:
            scales.append((r.qty["m2"] / px) ** 0.5)
    for kind, text in (("images", None), ("labels", "\n".join(lines)),
                       ("meta", json.dumps({"m_per_px": statistics.median(scales) if scales else None, "width": w, "height": h}))):
        out = Path(root) / kind / split / (stem + {"images": ".png", "labels": ".txt", "meta": ".json"}[kind])
        out.parent.mkdir(parents=True, exist_ok=True)
        im.crop((x0, y0, x1, y1)).save(out) if text is None else out.write_text(text)
    return stem, split, len(rooms)


def swiss_labels(out_tar: str) -> None:
    """Every Swiss plan as a training image with polygon labels, in one tar for `modal volume put`."""
    import json
    import multiprocessing
    import shutil
    import tempfile
    from collections import Counter
    from pathlib import Path

    from blueprints import pipeline

    types = json.loads((Path(__file__).parent.parent / "brain" / "room_types.json").read_text(encoding="utf-8"))
    pdfs = sorted((pipeline.DATA_ROOT / "data" / "swiss_dwellings").glob("*.pdf"))
    with tempfile.TemporaryDirectory() as root, multiprocessing.Pool() as pool:
        done = [d for d in pool.imap_unordered(_swiss_plan, [(str(p), root, types) for p in pdfs], chunksize=8) if d]
        shutil.make_archive(out_tar.removesuffix(".tar"), "tar", root)
    print(f"{len(pdfs)} plans, {dict(Counter(s for _, s, _ in done))}, {sum(n for _, _, n in done)} rooms -> {out_tar}")


def unpack() -> str:
    """The dataset on local disk: images/ and labels/ for train and val, plus the Swiss test split and its scales."""
    import glob
    import shutil

    vol.reload()
    for t in sorted(glob.glob(f"{CHUNKS}/*.tar")):
        shutil.unpack_archive(t, "/tmp/seg")
    open("/tmp/seg/data.yaml", "w").write(f"path: /tmp/seg\ntrain: images/train\nval: images/val\nnames: {NAMES}\n")
    return "/tmp/seg"


@app.function(image=det.gpu, volumes={"/vol": vol}, gpu="L40S", timeout=4 * 3600, cpu=8, memory=32768)
def train(name: str = RUN, model: str = "yolo11s-seg.pt", epochs: int = 35, imgsz: int = 1024, batch: int = 16):
    import os

    from ultralytics import YOLO

    root = unpack()
    print({s: len(os.listdir(f"{root}/images/{s}")) for s in ("train", "val")}, flush=True)
    YOLO(model).train(
        data=f"{root}/data.yaml", imgsz=imgsz, epochs=epochs, batch=batch, workers=8, patience=epochs,
        fliplr=0.5, flipud=0.5, degrees=0, scale=0.5, mosaic=1.0, close_mosaic=5,  # as the detector trains
        project=RUNS, name=name, exist_ok=True,
    )
    vol.commit()


def predict(model, im, conf: float = 0.25, imgsz: int = 1024) -> list[dict]:
    """Room outlines of one PIL image: [{cls, score, polygon 0..1}]. One region gets one class (agnostic NMS).
    imgsz 1536 was tried on whole Swiss A3 sheets: the same rooms found and the same area error as 1024."""
    import cv2
    import numpy as np

    if max(im.size) > 2048:  # ponytail: one pass; rooms are large, so a big sheet loses little. Tile if small rooms go missing
        im = im.resize((round(im.width * 2048 / max(im.size)), round(im.height * 2048 / max(im.size))))
    r = model.predict(im, imgsz=imgsz, conf=conf, agnostic_nms=True, retina_masks=True, verbose=False)[0]
    rooms = []
    for mask, c, s in zip(r.masks.data.cpu().numpy() if r.masks is not None else [], r.boxes.cls.tolist(), r.boxes.conf.tolist()):
        contours, _ = cv2.findContours((mask > 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        # a room is a few corners, not a staircase of mask pixels
        pts = cv2.approxPolyDP(max(contours, key=cv2.contourArea), max(1.5, 0.003 * max(mask.shape)), True).reshape(-1, 2).tolist()
        if len(pts) >= 3:
            rooms.append({"cls": NAMES[int(c)], "score": s, "polygon": [[x / mask.shape[1], y / mask.shape[0]] for x, y in pts]})
    return rooms


def _area(poly) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))) / 2


def match(gold: list, pred: list, side: int = 512) -> list[tuple[int, int, float]]:
    """(gold index, pred index, IoU) pairs with IoU >= 0.5, each polygon used once, best first. Polygons are 0..1."""
    import numpy as np
    from PIL import Image, ImageDraw

    def raster(poly):
        im = Image.new("1", (side, side))
        ImageDraw.Draw(im).polygon([(x * side, y * side) for x, y in poly], fill=1)
        return np.asarray(im)

    g, p = [raster(q) for q in gold], [raster(q) for q in pred]
    ious = sorted(((float((a & b).sum()) / max(float((a | b).sum()), 1), i, j) for i, a in enumerate(g) for j, b in enumerate(p)), reverse=True)
    out, used_g, used_p = [], set(), set()
    for iou, i, j in ious:
        if iou >= 0.5 and i not in used_g and j not in used_p:
            used_g.add(i)
            used_p.add(j)
            out.append((i, j, iou))
    return out


@app.function(image=det.gpu, volumes={"/vol": vol}, gpu="T4", timeout=3600)
def evaluate(name: str = RUN, limit: int = 0) -> dict:
    """Room-level precision, recall and F1 at IoU 0.5, mean IoU of the matched rooms and how often the type is right,
    per source and split; on Swiss also the area error of matched outlines in m2 (the scale is known from the DXF).
    The served polygons are scored, after simplification, not the raw masks."""
    import glob
    import json
    import os
    import statistics

    from PIL import Image
    from ultralytics import YOLO

    root, model, report = unpack(), YOLO(f"{RUNS}/{name}/weights/best.pt"), {}
    for source, split in (("cubicasa", "val"), ("swiss", "val"), ("swiss", "test")):
        images = sorted(f for f in glob.glob(f"{root}/images/{split}/*.png") if os.path.basename(f).startswith("swiss-") == (source == "swiss"))
        images = images[:limit] if limit else images
        n_gold = n_pred = hit = typed = 0
        ious, err_m2, err_rel = [], [], []
        for f in images:
            rows = [l.split() for l in open(f.replace("/images/", "/labels/")[:-4] + ".txt").read().splitlines() if l]
            gold = [(int(r[0]), [(float(x), float(y)) for x, y in zip(r[1::2], r[2::2])]) for r in rows]
            pred = predict(model, Image.open(f).convert("RGB"))
            pairs = match([g for _, g in gold], [p["polygon"] for p in pred])
            n_gold, n_pred, hit = n_gold + len(gold), n_pred + len(pred), hit + len(pairs)
            ious += [iou for _, _, iou in pairs]
            typed += sum(NAMES[gold[i][0]] == pred[j]["cls"] for i, j, _ in pairs)
            meta = f.replace("/images/", "/meta/")[:-4] + ".json"
            if os.path.exists(meta) and (m := json.load(open(meta)))["m_per_px"]:
                k = m["width"] * m["height"] * m["m_per_px"] ** 2  # 0..1 area -> m2
                for i, j, _ in pairs:
                    a, b = _area(gold[i][1]) * k, _area(pred[j]["polygon"]) * k
                    err_m2.append(abs(b - a))
                    err_rel.append(abs(b - a) / a)
        p, r = hit / max(n_pred, 1), hit / max(n_gold, 1)
        row = {"plans": len(images), "rooms": n_gold, "precision": round(p, 3), "recall": round(r, 3),
               "f1": round(2 * p * r / max(p + r, 1e-9), 3), "mean_iou_matched": round(statistics.mean(ious), 3) if ious else None,
               "type_right_of_matched": round(typed / max(hit, 1), 3)}
        if err_rel:
            row |= {"area_err_median_m2": round(statistics.median(err_m2), 2), "area_err_median_pct": round(100 * statistics.median(err_rel), 1),
                    "area_within_2pct": round(sum(e <= 0.02 for e in err_rel) / len(err_rel), 3),
                    "area_within_5pct": round(sum(e <= 0.05 for e in err_rel) / len(err_rel), 3)}
        report[f"{source}:{split}"] = row
        print(f"{source}:{split}", row, flush=True)
    return report


# A cold start loads one model on a T4; warm calls take about a second.
@app.cls(image=det.gpu, volumes={"/vol": vol}, gpu="T4", scaledown_window=300)
class Segmenter:
    @modal.enter()
    def load(self):
        from ultralytics import YOLO

        self.model = YOLO(f"{RUNS}/{RUN}/weights/best.pt")

    @modal.method()
    def segment(self, png: bytes) -> dict:
        """Room outlines of a plan image (PNG or JPG bytes), 0..1 of the image, with the GPU seconds spent."""
        import io
        import time

        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 300_000_000
        t = time.perf_counter()
        im = Image.open(io.BytesIO(png)).convert("RGB")
        return {"rooms": predict(self.model, im), "width": im.width, "height": im.height, "model": RUN, "gpu": "T4",
                "seconds": time.perf_counter() - t}


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["swiss"]:
        swiss_labels(sys.argv[2])
        raise SystemExit
    name = lambda *a: NAMES[room_class(*a)]
    assert (name("Space Bath", "WC"), name("Space Bath Shower", "PH"), name("Space Bedroom", "MH")) == ("wc_room", "bathroom", "bedroom")
    assert (name("Space Outdoor Balcony Glazed"), name("Space Outdoor Terrace"), name("Space Sauna", "S")) == ("balcony", "other", "sauna")
    assert (name("Space LivingRoom", "OH"), name("Space Entry Lobby", "ET"), name("Space Closet", "VH"), name("Space Undefined")) == ("living", "corridor", "storage", "other")
    assert room_class("Door Swing Beside") is None and room_class("Space") is None
    assert label_line(3, [(0, 0), (1.2, 0.5), (0.5, -1)]) == "3 0.00000 0.00000 1.00000 0.50000 0.50000 0.00000"
    sq =lambda x, y, s=0.2: [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    assert abs(_area(sq(0.1, 0.1)) - 0.04) < 1e-9
    # two gold rooms; one found slightly shifted, one missed, one false positive elsewhere
    pairs = match([sq(0.1, 0.1), sq(0.6, 0.6)], [sq(0.7, 0.1), sq(0.11, 0.11)])
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)] and 0.8 < pairs[0][2] < 1
    print("ok")
