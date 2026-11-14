"""How often answers were right in our own eval, by route and confidence band, so the answer card can say
"answers like this one were right 41 of 47 times" next to the confidence it shows.

`python -m blueprints.reliability` rebuilds reliability.json from runs/*.jsonl (pipeline and vision runs only, the
latest row per question). The JSON is committed because the deployed API has no runs/.
"""
import json
from pathlib import Path

FILE = Path(__file__).with_name("reliability.json")
MIN_ROWS = 10  # fewer scored answers in a band than this and we say nothing about it


def band(confidence: float) -> int:
    return min(9, int(confidence * 10))  # 0 = 0-9%, ..., 9 = 90-100%


def build(rows: list[dict]) -> dict[str, list[int]]:
    """{"<tier>:<band>": [right, scored]} over answered, auto-scored rows."""
    table: dict[str, list[int]] = {}
    for r in rows:
        if r["pred"] is None or r["correct"] is None:
            continue
        cell = table.setdefault(f"{r['tier']}:{band(r['confidence'])}", [0, 0])
        cell[0] += bool(r["correct"])
        cell[1] += 1
    return table


def measured(tier: str, confidence: float) -> str | None:
    try:
        table = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return None
    right, scored = table.get(f"{tier}:{band(confidence)}", (0, 0))
    if scored < MIN_ROWS:
        return None
    lo = band(confidence) * 10
    return (f"In our eval, answers from this route scored {lo}–{lo + 9 if lo < 90 else 100}% were right "
            f"{right} of {scored} times ({right / scored:.0%}).")


if __name__ == "__main__":
    latest: dict[tuple, dict] = {}
    for run in sorted(Path("runs").glob("*.jsonl")):  # names start with a timestamp, so later runs win
        kind = "vision" if "vision" in run.name else "pipeline" if "pipeline" in run.name else None
        if kind:  # a vision run measures another route on the same questions: keep both
            for line in run.read_text().splitlines():
                row = json.loads(line)
                latest[(kind, row["path"], row["question"])] = row
    table = build(list(latest.values()))
    FILE.write_text(json.dumps(dict(sorted(table.items())), indent=0) + "\n")
    assert build([{"pred": 3, "correct": True, "tier": "detect", "confidence": 1.0},
                  {"pred": None, "correct": False, "tier": "detect", "confidence": 0.5}]) == {"detect:9": [1, 1]}
    print(f"{FILE.name}: {sum(n for _, n in table.values())} scored answers from {len(latest)} questions")
    for key, (right, n) in sorted(table.items()):
        print(f"  {key:10} {right:4}/{n:<4} {right / n:.0%}")
