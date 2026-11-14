"""Look-alikes: the user rejects one detection in the viewer, and this finds the others that show the same symbol.

Template matching on the page image, with no model call. A CAD symbol (a chair, a table) is drawn at one size all over
a sheet, turned and mirrored, and the detector's box sits a little differently on each copy. So the rejected box is
compared in its 8 rotations and mirror images, at the drawing's own scale, at every position around each candidate.

Measured on a dormitory sheet where 21 of 162 door boxes sat on armchairs and round tables: against one rejected
chair box the other 20 scored 0.66-0.91 and every real door 0.50 or less.
"""
SIDE = 24  # pixels the rejected box's long side is shrunk to; everything compared is shrunk by the same factor
ALIKE = 0.6  # correlation from which two boxes show the same symbol; see the measurement above before moving it
MARGIN = 0.5  # how far around a candidate to search, as a share of the rejected box's long side
SIZE_RATIO = 3  # a candidate this many times larger or smaller is another symbol; it also bounds the work per box

Box = tuple[float, float, float, float]  # x0, y0, x1, y1, each 0..1 of the page


def gray(file):
    """A page image as a PIL "L" image."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 300_000_000  # as the detector: an A1 scan at 600 dpi is ~250 MP
    return Image.open(file).convert("L")


def _crop(image, box: Box):
    W, H = image.size
    return image.crop((round(box[0] * W), round(box[1] * H), round(box[2] * W), round(box[3] * H)))


def look_alikes(image, box: Box, others: dict[str, Box]) -> list[str]:
    """Keys of `others` that show the same symbol as `box`, most alike first. image: the page as a PIL "L" image."""
    return match(image, _crop(image, box), others)


def match(image, symbol, others: dict[str, Box]) -> list[str]:
    """Keys of `others` that show `symbol`, most alike first. symbol: a PIL "L" crop at the page's pixel scale,
    cut from this page or from another sheet drawn at the same scale."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    from PIL import Image

    W, H = image.size
    side = max(symbol.size)
    if min(symbol.size) < 2:
        return []
    shrink = max(side / SIDE, 1.0)

    def shrunk(im):
        size = (max(round(im.width / shrink), 2), max(round(im.height / shrink), 2))
        return np.asarray(im.resize(size, Image.BOX), dtype=float)

    def patch(x0, y0, x1, y1):
        return shrunk(image.crop((round(x0), round(y0), round(x1), round(y1))))

    def unit(a, axes):  # zero mean, unit length: the dot product of two of these is their correlation
        a = a - a.mean(axis=axes, keepdims=True)
        return a / np.maximum(np.sqrt((a * a).sum(axis=axes, keepdims=True)), 1e-6)

    template = shrunk(symbol)
    views = [unit(np.rot90(m, k), (0, 1)) for m in (template, template.T) for k in range(4)]
    scores = {}
    for key, (a0, b0, a1, b1) in others.items():
        a0, b0, a1, b1 = a0 * W, b0 * H, a1 * W, b1 * H
        if not side / SIZE_RATIO <= max(a1 - a0, b1 - b0) <= side * SIZE_RATIO:
            continue
        cx, cy = (a0 + a1) / 2, (b0 + b1) / 2
        hw, hh = max(a1 - a0, side) / 2 + side * MARGIN, max(b1 - b0, side) / 2 + side * MARGIN
        around = patch(cx - hw, cy - hh, cx + hw, cy + hh)  # at least twice the template each way, so every view fits
        best = max(float((unit(sliding_window_view(around, v.shape), (2, 3)) * v).sum(axis=(2, 3)).max()) for v in views)
        if best >= ALIKE:
            scores[key] = best
    return sorted(scores, key=scores.get, reverse=True)


# ---- memory: symbols the user rejected, left out of what the detector finds on later uploads ----
# One gray crop per rejected symbol in <cache>/rejected/<kind>/<id>.png, kind as "door" or "fixture-toilet".
# ponytail: one memory for the whole server, and a crop only matches sheets drawn at the same pixels per metre (the
# other floors of a set). Store the drawing scale with the crop and resize it when sheets at other scales matter.
MAX_REMEMBERED = 40  # per kind; each one costs about a second of matching per upload


def _folder(kind: str):
    from .. import pipeline
    return pipeline.CACHE / "rejected" / kind.replace(":", "-")


def remember(image, box: Box, kind: str) -> str | None:
    """Keep the symbol in `box` as "not a <kind>". Returns its id for forget(), or None when a remembered symbol
    already matches it or the memory for this kind is full."""
    import hashlib
    import io

    from PIL import Image

    symbol, folder = _crop(image, box), _folder(kind)
    known = sorted(folder.glob("*.png")) if folder.is_dir() else []
    if min(symbol.size) < 2 or len(known) >= MAX_REMEMBERED or any(match(image, Image.open(f), {"it": box}) for f in known):
        return None
    data = io.BytesIO()
    symbol.save(data, "PNG")
    name = hashlib.sha256(data.getvalue()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.png").write_bytes(data.getvalue())
    return name


def forget(kind: str, name: str) -> None:
    (_folder(kind) / f"{name}.png").unlink(missing_ok=True)


def leave_out_rejected(scene, file) -> int:
    """Set aside the detections on a new drawing that show a remembered symbol. They stay in the scene, retyped to
    "other" with the reason as their label, so nothing counts them and the viewer still shows what was left out."""
    from PIL import Image

    from .. import pipeline
    root, page, left_out = pipeline.CACHE / "rejected", None, 0
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        kind, _, subtype = folder.name.partition("-")
        found = {e.id: e for e in scene.entities if e.type == kind and (e.subtype or "") == subtype and len(e.geom) >= 3}
        boxes = {i: (min(p[0] for p in e.geom), min(p[1] for p in e.geom), max(p[0] for p in e.geom), max(p[1] for p in e.geom))
                 for i, e in found.items()}
        for symbol in sorted(folder.glob("*.png")):
            if not boxes:
                break
            page = page or gray(file)
            for i in match(page, Image.open(symbol), boxes):
                e = found[i]
                e.label = f"not a {subtype or kind}: it shows a symbol rejected on another drawing"
                e.attrs["rejected_as"] = folder.name
                e.type, e.subtype = "other", None
                del boxes[i]
                left_out += 1
    return left_out


if __name__ == "__main__":
    from PIL import Image, ImageDraw

    page = Image.new("L", (1200, 800), 215)
    draw = ImageDraw.Draw(page)

    def chair(x, y, turn=0, mirror=False):  # a round table with a chair leg beside it, 60 px, drawn like the sheet's
        tile = Image.new("L", (60, 60), 215)
        d = ImageDraw.Draw(tile)
        d.ellipse((4, 14, 40, 50), fill=255, outline=0, width=3)
        d.polygon([(44, 50), (56, 22), (60, 26), (50, 56)], fill=255, outline=0)
        tile = tile.transpose(Image.FLIP_LEFT_RIGHT) if mirror else tile
        page.paste(tile.rotate(90 * turn), (x, y))
        return (x / 1200, y / 800, (x + 60) / 1200, (y + 60) / 800)

    def door(x, y):  # a wall, a leaf and its thin swing arc
        draw.line((x, y, x + 60, y), fill=0, width=5)
        draw.line((x, y, x, y + 60), fill=110, width=2)
        draw.arc((x - 60, y - 60, x + 60, y + 60), 0, 90, fill=140, width=2)
        return (x / 1200, y / 800, (x + 60) / 1200, (y + 60) / 800)

    def nudged(b, dx, dy):  # the detector's box, off the symbol by a few pixels
        return (b[0] + dx / 1200, b[1] + dy / 800, b[2] + dx / 1200, b[3] + dy / 800)

    rejected = chair(100, 100)
    others = {"turned": nudged(chair(400, 100, turn=1), 7, -5), "mirrored": nudged(chair(700, 100, mirror=True), -6, 8),
              "upside": chair(1000, 100, turn=2), "door-1": door(100, 500), "door-2": door(400, 500),
              "huge": (0.0, 0.0, 0.9, 0.9), "blank": (0.5, 0.8, 0.55, 0.875)}
    found = look_alikes(page, rejected, others)
    assert sorted(found) == ["mirrored", "turned", "upside"], found
    # a rejected real door finds the other door and no chairs; a box on blank paper finds nothing
    assert look_alikes(page, others["door-1"], {k: v for k, v in others.items() if k != "door-1"}) == ["door-2"]
    assert look_alikes(page, others["blank"], others) == [] and look_alikes(page, (0.5, 0.5, 0.5, 0.5), others) == []

    # memory: a chair rejected as a door is set aside on the next drawing; its doors stay; forgetting restores it
    import tempfile
    from pathlib import Path

    from .. import pipeline
    from ..schemas import Entity, Page, Scene
    with tempfile.TemporaryDirectory() as tmp:
        pipeline.CACHE = Path(tmp)
        page.save(Path(tmp) / "next.png")
        corners = lambda b: [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]

        def detected():  # the detector called every chair and door a door
            return Scene(path="uploads/next.png", builder="t", tier="detect", covers=["door"], pages=[Page(image="uploads/next.png", width=1200, height=800)],
                         entities=[Entity(id=k, type="door", geom=corners(b)) for k, b in others.items() if k not in ("huge", "blank")])

        name = remember(page, rejected, "door")
        assert name and remember(page, others["turned"], "door") is None  # the same symbol is kept once
        scene = detected()
        assert leave_out_rejected(scene, Path(tmp) / "next.png") == 3
        assert sorted(e.id for e in scene.entities if e.type == "door") == ["door-1", "door-2"]
        assert all(e.label.startswith("not a door") for e in scene.entities if e.type == "other")
        Scene.model_validate(scene.model_dump())
        forget("door", name)
        assert leave_out_rejected(detected(), Path(tmp) / "next.png") == 0
    print("ok")
