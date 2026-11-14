"""Corrections made in the viewer, kept as labels for training the detector.

Nothing is recorded on its own. In Correct mode the user presses "Record for training", and the viewer sends how the
drawing now differs from what the reader found: boxes the user drew (added), dragged to a new size (resized), removed
as wrong (removed) or confirmed as right (confirmed), each with the page image it is on, the class and the detector's
score. A recording is appended to <cache>/labels.jsonl as a header row and one row per label; recording a drawing
again replaces what was recorded for it before, so the file is append-only and load() reads the latest state.

    uv run python -m blueprints.detector.labels            self-check
    from blueprints.detector.labels import load            rows for a training set

Boxes are x0, y0, x1, y1, each 0..1 of the image; `image` is a Page.image ("uploads/<id>.png", "data/...",
"pages/..."), resolved under the cache or the dataset as the API does. A negative is a hard negative: the detector
called it `type` and a person said it is not.
"""
import json
import threading
import time
import uuid

_lock = threading.Lock()


def _file():
    from .. import pipeline
    return pipeline.CACHE / "labels.jsonl"


def save(drawing: str, labels: list[dict]) -> None:
    """Record the corrections on one drawing, replacing any recorded for it before. labels: dicts with action,
    image, entity, type, subtype, box, and optionally was (the box before a resize) and conf (the detector's score).
    An empty list clears the drawing's labels."""
    recording = uuid.uuid4().hex
    head = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "drawing": drawing, "recording": recording, "labels": len(labels)}
    with _lock:
        _file().parent.mkdir(parents=True, exist_ok=True)
        with _file().open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in [head, *({"recording": recording, **label} for label in labels)])


def load() -> list[dict]:
    """The labels of each drawing's latest recording: {drawing, image, entity, type, subtype, box, positive, conf}.
    A removed detection is a negative; anything added, resized or confirmed is a positive with its box as it is now."""
    if not _file().exists():
        return []
    rows = [json.loads(line) for line in _file().read_text(encoding="utf-8").splitlines() if line.strip()]
    latest = {r["recording"]: r["drawing"] for r in {r["drawing"]: r for r in rows if "drawing" in r}.values()}
    return [{"drawing": latest[r["recording"]], "image": r["image"], "entity": r["entity"], "type": r["type"],
             "subtype": r.get("subtype"), "box": r["box"], "positive": r["action"] != "removed", "conf": r.get("conf")}
            for r in rows if "drawing" not in r and r.get("recording") in latest]  # rows from before recordings are skipped


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from .. import pipeline
    with tempfile.TemporaryDirectory() as tmp:
        pipeline.CACHE = Path(tmp)
        assert load() == []
        row = lambda action, entity, box=(0.1, 0.1, 0.2, 0.2), **more: {"action": action, "image": "uploads/a.png", "entity": entity, "type": "door", "box": list(box), **more}
        # first recording: two chairs taken for doors, and a door the user drew
        save("uploads/a.png", [row("removed", "det-1", conf=0.4), row("removed", "det-2", conf=0.5), row("added", "user-1")])
        save("uploads/b.png", [row("confirmed", "det-9", conf=0.6)])
        assert len(load()) == 4
        # the user puts det-2 back, resizes the drawn door and records again: only the new state of a.png counts
        save("uploads/a.png", [row("removed", "det-1", conf=0.4), row("resized", "user-1", (0.1, 0.1, 0.3, 0.3), was=[0.1, 0.1, 0.2, 0.2])])
        got = {(label["drawing"], label["entity"]): label for label in load()}
        assert sorted(got) == [("uploads/a.png", "det-1"), ("uploads/a.png", "user-1"), ("uploads/b.png", "det-9")], sorted(got)
        assert not got["uploads/a.png", "det-1"]["positive"] and got["uploads/a.png", "det-1"]["conf"] == 0.4
        assert got["uploads/a.png", "user-1"]["positive"] and got["uploads/a.png", "user-1"]["box"] == [0.1, 0.1, 0.3, 0.3]
        assert got["uploads/b.png", "det-9"]["positive"]
        save("uploads/a.png", [])  # everything undone and recorded again: a.png has no labels left
        assert [label["drawing"] for label in load()] == ["uploads/b.png"]
    print("ok")
