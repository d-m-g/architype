"""Measured confidence. A raw score (a detector's box scores, a parsed 1.0, the vision reader's fixed 0.7) becomes the
rate at which answers like it were right on the train split, so a shown 80% means right about 8 times in 10.

`python -m blueprints.calibration` fits calibration.json from runs/calib-train-*.jsonl. Those runs must log raw
scores: make them with BLUEPRINTS_RAW_CONFIDENCE=1 once calibration.json exists. The card's "in our eval" line
(reliability.py) stays on the test split, which this never sees.
"""
import json
import os
from pathlib import Path

from .schemas import Answer

FILE = Path(__file__).with_name("calibration.json")
RAW = os.environ.get("BLUEPRINTS_RAW_CONFIDENCE") == "1"


def _kind(unit) -> str:
    return "count" if unit == "count" else "measure"


def _rate(right: int, n: int) -> float:
    return (right + 1) / (n + 2)  # a few rows cannot claim 0% or 100%


def isotonic(points: list[tuple[float, bool]]) -> list[list[float]]:
    """Pool adjacent violators: [[highest raw score in the step, rate, rows], ...], rates rising with the score."""
    blocks = []  # [top score, right, n]
    for score, ok in sorted(points):
        blocks.append([score, int(ok), 1])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] >= blocks[-1][1] / blocks[-1][2]:
            top, right, n = blocks.pop()
            blocks[-1] = [top, blocks[-1][1] + right, blocks[-1][2] + n]
    out, floor = [], 0.0
    for top, right, n in blocks:  # smoothing can dip a small top block (2 of 2 -> 75%) below the one under it
        floor = max(floor, _rate(right, n))
        out.append([round(top, 4), round(floor, 4), n])
    return out


def fit(rows: list[dict], route: str) -> dict:
    """What one route's train rows say. route: parse, detect or vision."""
    rows = [r for r in rows if r["pred"] is not None and r["correct"] is not None]
    out: dict = {}
    for kind in ("count", "measure"):
        mine = [r for r in rows if _kind(r["pred_unit"]) == kind]
        if route == "detect" and kind == "count":
            zero = [r for r in mine if r["pred"] == 0]
            out["zero"] = [round(_rate(sum(r["correct"] for r in zero), len(zero)), 4), len(zero)]
            out["count"] = isotonic([(r["confidence"], r["correct"]) for r in mine if r["pred"] != 0])
        elif mine:
            out[kind] = [round(_rate(sum(r["correct"] for r in mine), len(mine)), 4), len(mine)]
    return out


def _table() -> dict:
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def calibrate(answer: Answer, route: str) -> Answer:
    """The answer with its raw confidence replaced by the measured rate, and the note saying so. Unchanged when the
    route has no measurement for this kind of answer, or for raw-score runs."""
    fitted = _table().get(route, {})
    kind = _kind(answer.unit)
    if RAW or answer.answer is None or kind not in fitted:
        return answer
    raw = answer.confidence
    if route == "detect" and kind == "count":
        if answer.answer == 0:
            rate, n = fitted["zero"]
            why = f"A count of zero: such answers were right {rate:.0%} of the time on {n} training questions."
        else:
            steps = fitted["count"]
            step = next((s for s in steps if raw <= s[0]), steps[-1])
            rate, n = step[1], step[2]
            why = f"Calibrated: raw score {raw:.0%} becomes {rate:.0%}, the rate answers with that score were right on training questions ({n} in its step)."
    else:
        rate, n = fitted[kind]
        why = f"Calibrated: {route} answers of this kind were right {rate:.0%} of the time on {n} training questions."
    return answer.model_copy(update={"confidence": rate, "confidence_note": f"{answer.confidence_note or ''} {why}".strip()})


if __name__ == "__main__":
    assert isotonic([(0.1, False), (0.2, True), (0.3, False), (0.9, True)]) == [[0.1, 0.3333, 1], [0.3, 0.5, 2], [0.9, 0.6667, 1]]
    route_of = {"parse": "parse", "detect": "detect", "llm": "vision"}
    rows: dict[str, list] = {}
    for run in sorted(Path("runs").glob("calib-train-*.jsonl")):
        for line in run.read_text().splitlines():
            r = json.loads(line)
            if "vision" in run.name and r["tier"] != "llm":
                continue
            if "vision" not in run.name and r["tier"] == "llm":
                continue  # escalated answers in a pipeline run: the agent, which is not calibrated here
            rows.setdefault("vision" if "vision" in run.name else route_of.get(r["tier"], r["tier"]), []).append(r)
    table = {route: fit(rs, route) for route, rs in rows.items() if route in ("parse", "detect", "vision")}
    FILE.write_text(json.dumps(table, indent=1) + "\n")
    print(json.dumps(table, indent=1))
