"""Corrections made in the viewer, kept as labels for training the detector.

Every correction in Correct mode is appended to <cache>/labels.jsonl: a box the user drew (added), dragged to a new
size (resized), removed as wrong (removed) or confirmed as right (confirmed), with the page image it is on, the class
and the detector's score. An Undo in the viewer appends a line naming the correction it takes back, so the file is
append-only. load() gives the net result: one positive or negative box per item.

    uv run python -m blueprints.detector.labels            self-check
    from blueprints.detector.labels import load            rows for a training set

Boxes are x0, y0, x1, y1, each 0..1 of the image; `image` is a Page.image ("uploads/<id>.png", "data/...",
"pages/..."), resolved under the cache or the dataset as the API does. A negative is a hard negative: the detector
called it `type` and a person said it is not.
"""
import json
import threading
import time

_lock = threading.Lock()


def _file():
    from .. import pipeline
    return pipeline.CACHE / "labels.jsonl"


def _append(rows: list[dict]) -> None:
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _lock:
        _file().parent.mkdir(parents=True, exist_ok=True)
        with _file().open("a", encoding="utf-8") as f:
            f.writelines(json.dumps({"at": at, **row}, ensure_ascii=False) + "\n" for row in rows)


def save(correction: str, labels: list[dict]) -> None:
    """labels: dicts with action, image, entity, type, subtype, box, and optionally was (the box before a resize)
    and conf (the detector's score). correction: the viewer's id for this one user action."""
    _append([{"correction": correction, **label} for label in labels])


def undo(correction: str) -> None:
    _append([{"correction": correction, "undo": True}])


def load() -> list[dict]:
    """The net labels: {image, entity, type, subtype, box, positive, conf} per item, after undos. An item drawn and
    later removed leaves nothing; a detection removed leaves a negative; anything added, resized or confirmed is a
    positive with its latest box."""
    if not _file().exists():
        return []
    rows = [json.loads(line) for line in _file().read_text(encoding="utf-8").splitlines() if line.strip()]
    undone = {r["correction"] for r in rows if r.get("undo")}
    net: dict[tuple, dict] = {}
    for r in rows:
        if r.get("undo") or r["correction"] in undone:
            continue
        key = (r["image"], r["entity"])
        drawn = net.get(key, {}).get("drawn", False) or r["action"] == "added"  # by the user, not the detector
        if r["action"] == "removed" and drawn:
            net.pop(key, None)
            continue
        net[key] = {"image": r["image"], "entity": r["entity"], "type": r["type"], "subtype": r.get("subtype"),
                    "box": r["box"], "positive": r["action"] != "removed", "conf": r.get("conf"), "drawn": drawn}
    return [{k: v for k, v in label.items() if k != "drawn"} for label in net.values()]


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from .. import pipeline
    with tempfile.TemporaryDirectory() as tmp:
        pipeline.CACHE = Path(tmp)
        assert load() == []
        row = lambda action, entity, box=(0.1, 0.1, 0.2, 0.2), **more: {"action": action, "image": "uploads/a.png", "entity": entity, "type": "door", "subtype": None, "box": list(box), **more}
        save("c1", [row("removed", "det-1", conf=0.4), row("removed", "det-2", conf=0.5)])  # two chairs taken for doors
        save("c2", [row("added", "user-1")])
        save("c3", [row("resized", "user-1", (0.1, 0.1, 0.3, 0.3), was=[0.1, 0.1, 0.2, 0.2])])
        save("c4", [row("confirmed", "det-3", conf=0.6)])
        save("c5", [row("added", "user-2")])
        save("c6", [row("removed", "user-2")])  # drawn by mistake and removed: no label at all
        save("c7", [row("removed", "det-4", conf=0.9)])
        undo("c7")  # a real door removed by mistake, then undone
        got = {label["entity"]: label for label in load()}
        assert sorted(got) == ["det-1", "det-2", "det-3", "user-1"], sorted(got)
        assert not got["det-1"]["positive"] and got["det-1"]["conf"] == 0.4 and got["det-3"]["positive"]
        assert got["user-1"]["positive"] and got["user-1"]["box"] == [0.1, 0.1, 0.3, 0.3]
    print("ok")
