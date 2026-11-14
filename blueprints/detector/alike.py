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


def look_alikes(image, box: Box, others: dict[str, Box]) -> list[str]:
    """Keys of `others` that show the same symbol as `box`, most alike first. image: the page as a PIL "L" image."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    from PIL import Image

    W, H = image.size
    x0, y0, x1, y1 = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    side = max(x1 - x0, y1 - y0)
    if min(x1 - x0, y1 - y0) < 2:
        return []
    shrink = max(side / SIDE, 1.0)

    def patch(x0, y0, x1, y1):
        size = (max(round((x1 - x0) / shrink), 2), max(round((y1 - y0) / shrink), 2))
        return np.asarray(image.crop((round(x0), round(y0), round(x1), round(y1))).resize(size, Image.BOX), dtype=float)

    def unit(a, axes):  # zero mean, unit length: the dot product of two of these is their correlation
        a = a - a.mean(axis=axes, keepdims=True)
        return a / np.maximum(np.sqrt((a * a).sum(axis=axes, keepdims=True)), 1e-6)

    template = patch(x0, y0, x1, y1)
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
    print("ok")
