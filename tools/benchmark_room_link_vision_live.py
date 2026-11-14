"""Run two authenticated PNG pilot questions, then score all 26 room tasks locally.

Uses getpass and an in-memory cookie jar; never writes credentials. Needs the saved audit captures.
Only the two pilot questions (and optional cached repeat) make model-capable requests.
"""
import argparse
import getpass
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprints.executor import execute
from blueprints.schemas import Query, QueryPlan, Scene
from blueprints.brain.room_link_vision import VERSION
from tools.compare_png_room_links import GOLD


def score(scene, sample):
    rows = []
    for number, gold in GOLD[sample].items():
        room = f"vis-room-{number}"
        plan = QueryPlan(queries={"n": Query(op="count", type="door", where={"connected_room_id": room})}, unit="count")
        answer = execute(plan, scene)
        rows.append({"room": room, "gold": gold, "answer": answer.answer,
                     "status": "abstained" if answer.answer is None else "correct" if answer.answer == gold else "wrong",
                     "error": answer.error})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path)
    parser.add_argument("--base", default="https://blueprints.d-m-g.dev")
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(route, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(args.base+route, data=data, headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=200) as response:
            return json.load(response)

    password = getpass.getpass("Demo password (not stored): ")
    request("/api/login", {"password": password})
    del password
    out = Path(".cache")/("png-vision-live-"+time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)
    report = {"samples": [], "model_capable_requests": 0, "trace_cost_usd": 0, "refinement_cost_usd": 0}
    for attempt in range(48):
        try:
            if request("/api/health").get("room_link_refiner") == VERSION:
                break
        except urllib.error.HTTPError as error:
            if error.code not in (404, 502, 503, 504):
                raise
        if attempt % 6 == 0:
            print(f"Waiting for deployment {VERSION}...", flush=True)
        time.sleep(5)
    else:
        raise RuntimeError("Expected refiner deployment not observed; no model pilot sent")
    for sample, target in [("S01", "vis-room-7"), ("S02", "vis-room-14")]:
        reference = json.loads((args.audit/f"{sample}.json").read_text(encoding="utf-8"))
        path = reference["tested_path"]
        route = "/api/scene?"+urllib.parse.urlencode({"path": path})
        for attempt in range(36):
            try:
                before = request(route)
            except urllib.error.HTTPError as error:
                if error.code not in (502, 503, 504):
                    raise
                print(f"Deployment restarting ({error.code}); retrying read-only check...", flush=True)
                time.sleep(5)
                continue
            if any(e.get("attrs", {}).get("room_link_version") for e in before["entities"]):
                break
            if attempt % 6 == 0:
                print("Waiting for deployment with PNG connections...", flush=True)
            time.sleep(5)
        else:
            raise RuntimeError("New demo deployment not observed; no model pilot run")
        # A changed extraction needs a new gold mapping; do not silently reuse labels for different rooms.
        old_rooms = {e["id"]: (e.get("label"), e.get("geom")) for e in reference["scene"]["body"]["entities"] if e["type"] == "room"}
        new_rooms = {e["id"]: (e.get("label"), e.get("geom")) for e in before["entities"] if e["type"] == "room"}
        if old_rooms != new_rooms:
            raise RuntimeError(f"{sample} room extraction changed: gold ids must be reviewed before spending")
        (out/f"{sample}-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
        question = f'How many doors are connected to the room with internal room ID "{target}"? Use connected_room_id, not room number or label.'
        print(f"{sample}: running one real-model pilot for {target}", flush=True)
        answer = request("/api/ask", {"path": path, "question": question})
        report["model_capable_requests"] += 1
        (out/f"{sample}-pilot.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
        after = request(route)
        (out/f"{sample}-after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
        rows = score(Scene.model_validate(after), sample)
        item = {"sample": sample, "before": score(Scene.model_validate(before), sample), "after": rows,
                "pilot_answer": answer.get("answer"), "pilot_error": answer.get("error"),
                "cost_usd": sum(t.get("usd", 0) for t in answer.get("trace", [])),
                "refinement": [t for t in answer.get("trace", []) if t["step"] == "resolve_room_connections"]}
        report["samples"].append(item)
        report["trace_cost_usd"] += item["cost_usd"]
        report["refinement_cost_usd"] += sum(t.get("usd", 0) for t in item["refinement"])
        print(sample, {status: sum(r["status"] == status for r in rows) for status in ["correct", "wrong", "abstained"]},
              "refinement:", [{k: t.get(k) for k in ["detail", "usd", "ms"]} for t in item["refinement"]], flush=True)
        (out/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if any(r["status"] == "wrong" for r in rows):
            print("Stopping: incorrect model-assisted counts need investigation.", flush=True)
            break
        if any("Malformed model reply" in t.get("detail", "") for t in item["refinement"]):
            print("Stopping: malformed model output needs investigation before another paid pilot.", flush=True)
            break
        if answer.get("answer") is not None and not report.get("cached_repeat"):
            repeated = request("/api/ask", {"path": path, "question": question})
            report["model_capable_requests"] += 1
            report["cached_repeat"] = {"answer": repeated.get("answer"), "cost_usd": sum(t.get("usd", 0) for t in repeated.get("trace", [])),
                                       "refinement": [t for t in repeated.get("trace", []) if t["step"] == "resolve_room_connections"]}
            (out/f"{sample}-repeat.json").write_text(json.dumps(repeated, indent=2), encoding="utf-8")
    report["totals"] = {status: sum(r["status"] == status for s in report["samples"] for r in s["after"])
                        for status in ["correct", "wrong", "abstained"]}
    (out/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2), flush=True)
    print("Report:", (out/"report.json").resolve(), flush=True)
    return bool(report["totals"]["wrong"])


if __name__ == "__main__":
    sys.exit(main())
