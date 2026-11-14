"""Offline comparison on the two saved PNG audit scenes, without calling a model.

Run from repo root: python tools/compare_png_room_links.py <audit-directory> --baseline d7c4f26
The directory needs S01.json/S02.json API captures and S01-source.png/S02-source.png.
This measures scene/executor behaviour, not end-to-end natural-language/model accuracy.
Gold counts are a visual reading of these two drawings, not a representative held-out benchmark.
"""
import argparse
import json
import subprocess
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprints.executor import execute
from blueprints.image_room_links import attach_room_links
from blueprints.schemas import Query, QueryPlan, Scene

GOLD = {
    "S01": {2: 1, 3: 2, 4: 1, 5: 3, 6: 1, 7: 5, 8: 2, 9: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 2},
    "S02": {2: 1, 3: 1, 4: 2, 5: 1, 6: 5, 7: 2, 8: 1, 9: 3, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1},
}


def run(directory, baseline):
    module = types.ModuleType("blueprints._baseline_executor")
    code = subprocess.run(["git", "show", f"{baseline}:blueprints/executor.py"], check=True, capture_output=True, text=True).stdout
    exec(compile(code, "baseline_executor.py", "exec"), module.__dict__)
    report = {"baseline": baseline, "model_calls": 0, "model_cost_usd": 0, "scope": "offline scene/executor only",
              "samples": [], "global_regressions": [], "correct": 0, "wrong": 0, "abstained": 0}
    for name, gold in GOLD.items():
        captured = json.loads((directory/f"{name}.json").read_text(encoding="utf-8"))
        original = Scene.model_validate(captured["scene"]["body"])
        enriched = original.model_copy(deep=True)
        t = time.perf_counter()
        attach_room_links(enriched, directory/f"{name}-source.png")
        sample = {"sample": name, "link_ms": round((time.perf_counter()-t)*1000, 1), "queries": []}
        for kind, subtype in [("door", None), ("window", None), ("fixture", "toilet"), ("room", None)]:
            key = f"{kind}:{subtype}" if subtype else kind
            if key not in original.covers:
                continue
            plan = QueryPlan(queries={"n": Query(op="count", type=kind, subtype=subtype)}, unit="count")
            before, after = module.execute(plan, original), execute(plan, enriched)
            if (before.answer, before.evidence) != (after.answer, after.evidence):
                report["global_regressions"].append({"sample": name, "type": key})
        for number, expected in gold.items():
            room_id = f"vis-room-{number}"
            # The baseline has no endpoint-union filter. Its existing equivalent is exact attr matching.
            old = QueryPlan(queries={"n": Query(op="count", type="door", where={"room_id": room_id})}, unit="count")
            new = QueryPlan(queries={"n": Query(op="count", type="door", where={"connected_room_id": room_id})}, unit="count")
            before, after = module.execute(old, original), execute(new, enriched)
            status = "abstained" if after.answer is None else "correct" if after.answer == expected else "wrong"
            report[status] += 1
            sample["queries"].append({"room": room_id, "gold": expected, "baseline": before.answer,
                                      "after": after.answer, "status": status, "error": after.error})
        report["samples"].append(sample)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    result = run(args.directory, args.baseline)
    print(json.dumps(result, indent=2))
    sys.exit(bool(result["wrong"] or result["global_regressions"]))
