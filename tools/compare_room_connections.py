"""Replay one small paid main capture for frequent free branch checks. Raw scenes stay in ignored .cache/.

python tools/compare_room_connections.py --label checkpoint-2
"""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprints import pipeline
from blueprints.schemas import Scene


def git(*arguments, cwd=ROOT):
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="latest")
    parser.add_argument("--live-baseline", type=Path, default=ROOT/".cache/room-ranking-comparison/live-main.json")
    parser.add_argument("--main-root", type=Path, default=ROOT.parent/"room-ranking-main-baseline")
    parser.add_argument("--audit-root", type=Path, default=ROOT.parent/"dataset-review/audit-2026-11-14")
    parser.add_argument("--control-main-root", type=Path, help="Optional newer main checkout for compatibility checks; the paid live capture stays pinned")
    args = parser.parse_args()
    expected = json.loads((ROOT/"benchmarks/room_connections/expected.json").read_text(encoding="utf-8"))
    baseline = json.loads(args.live_baseline.read_text(encoding="utf-8"))
    if git("rev-parse", "HEAD", cwd=args.main_root) != expected["main_baseline_commit"]:
        raise SystemExit("Main baseline moved; review expectations before comparing.")
    if baseline["local_main_baseline"] != expected["main_baseline_commit"]:
        raise SystemExit("Live capture refers to another local main baseline.")
    rows = []
    for case in baseline["cases"]:
        reference = expected["cases"][case["id"]]
        source = Scene.model_validate(case["scene"])
        winning_rooms = [e for e in source.entities if e.type == "room" and [e.number, e.apartment] in reference["rooms"]]
        assert len(winning_rooms) == len(reference["rooms"]), "Reference room identity changed"
        kind = "window" if "windows" in case["question"].lower() else "door"
        expected_evidence = {room.id for room in winning_rooms}
        # Independent check of the explicitly reviewed room numbers against the raw endpoint records.
        for room in winning_rooms:
            attached = {e.id for e in source.entities if e.type == kind and e.page == room.page and
                        (room.number in (e.attrs.get("room"), e.attrs.get("room2")) or room.id in (e.attrs.get("room_id"), e.attrs.get("room_id2")))}
            assert len(attached) == reference["count"], "Reference connection count changed"
            expected_evidence.update(attached)
        timings = []
        with patch.object(pipeline, "get_scene", return_value=source), \
             patch("blueprints.brain.plan.plan_with_trace", side_effect=AssertionError("paid planner used by replay")), \
             patch("blueprints.brain.escalate.escalate", side_effect=AssertionError("paid escalation used by replay")):
            for _ in range(20):
                started = time.perf_counter()
                answer = pipeline.answer(case["path"], case["question"])
                timings.append((time.perf_counter()-started)*1000)
        winners = {e.id for e in source.entities if e.type == "room" and e.id in answer.evidence}
        evidence_ok = set(answer.evidence) == expected_evidence
        located = all(len(e.geom) >= 3 for e in source.entities if e.id in answer.evidence)
        correct = answer.answer == reference["count"] and winners == {e.id for e in winning_rooms} and evidence_ok and located
        rows.append({"id": case["id"], "main_answer": case["answer"].get("answer"),
                     "main_count_correct": case["answer"].get("answer") == reference["count"],
                     "main_seconds": case["seconds"], "main_reported_cost_usd": case["answer"].get("cost_usd", 0),
                     "branch_answer": answer.answer, "expected_count": reference["count"], "winner_count": len(winners),
                     "correct_count_winners_and_evidence": correct, "evidence_located": located,
                     "branch_median_ms": statistics.median(timings), "branch_incremental_model_cost_usd": answer.cost_usd})
    controls = []
    control_main = args.control_main_root or args.main_root
    for name, cwd in [("main", control_main), ("feature", ROOT)]:
        check = subprocess.run([sys.executable, "test_contract.py"], cwd=cwd, capture_output=True, text=True)
        controls.append({"branch": name, "passed": check.returncode == 0, "output": (check.stdout+check.stderr).strip()})
    safety = []
    for case_id in ("S11", "S14", "S16"):
        file = args.audit_root/(case_id+".json")
        if not file.exists():
            continue
        source = Scene.model_validate(json.loads(file.read_text(encoding="utf-8"))["scene"]["body"])
        with patch.object(pipeline, "get_scene", return_value=source), \
             patch("blueprints.brain.plan.plan_with_trace", side_effect=AssertionError("paid planner used for safety check")), \
             patch("blueprints.brain.escalate.escalate", side_effect=AssertionError("paid guessing used for safety check")):
            answer = pipeline.answer(source.path, "Which room has the most doors?")
        safety.append({"id": case_id, "abstained": answer.answer is None, "incremental_model_cost_usd": answer.cost_usd})
    digest = hashlib.sha256()
    for name in ["blueprints/room_connections.py", "blueprints/brain/escalate.py", "blueprints/pipeline.py"]:
        digest.update((ROOT/name).read_bytes())
    result = {"label": args.label, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "main_commit": expected["main_baseline_commit"], "feature_head": git("rev-parse", "HEAD"), "feature_files_sha256": digest.hexdigest(),
              "contract_main_commit": git("rev-parse", "HEAD", cwd=control_main),
              "reference_kind": expected["reference_kind"], "cases": rows, "contract_controls": controls,
              "unsuitable_scene_safety_checks": safety,
              "main_count_correct": sum(r["main_count_correct"] for r in rows), "feature_fully_correct": sum(r["correct_count_winners_and_evidence"] for r in rows),
              "live_baseline_reported_cost_usd": baseline["reported_cost_usd"], "additional_paid_model_calls": 0,
              "limits": "Live-demo answers are a timestamped black-box capture, not a controlled deterministic execution of the pinned local main. Branch timings exclude extraction and HTTP. Source-relationship references do not prove geometry detection completeness."}
    out = ROOT/".cache/room-ranking-comparison"/(args.label+".json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not all(r["correct_count_winners_and_evidence"] and r["branch_incremental_model_cost_usd"] == 0 for r in rows) or not all(c["passed"] for c in controls) or not all(s["abstained"] and s["incremental_model_cost_usd"] == 0 for s in safety):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
