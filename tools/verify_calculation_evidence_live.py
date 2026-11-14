"""Verify the reported corridor/legend regression locally and after demo deployment.

Credentials/cookies remain in memory. The offline replay only reuses existing model reviews.
"""
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
from blueprints.brain.room_link_vision import VALIDATION_VERSION, Link, _accept
from blueprints.calculation_evidence import deduplicate_corrected
from blueprints.executor import execute
from blueprints.schemas import Scene, Query, QueryPlan

BASE = "https://blueprints.d-m-g.dev"
PATH = "uploads/41fb89089508fe7150e3.png"
EXPECTED = {"det-0", "det-2", "det-8", "det-14", "det-16", "det-19", "det-25"}


def main():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(route, body=None):
        req = urllib.request.Request(BASE+route, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=180) as response:
            return json.load(response)
    password = getpass.getpass("Demo password (not stored): ")
    request("/api/login", {"password": password})
    del password
    route = "/api/scene?"+urllib.parse.urlencode({"path": PATH})
    raw = request(route)
    out = Path(".cache")/("calculation-evidence-live-"+time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)
    (out/"source.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
    scene = Scene.model_validate(raw)
    for entity in scene.entities:
        review = entity.attrs.get("room_link_review")
        if review:
            _accept(scene, entity, Link.model_validate_json(review))
    deduplicate_corrected(scene)
    local = execute(QueryPlan(queries={"n": Query(op="count", type="door", where={"connected_room_number": "4.05"})}, unit="count"), scene)
    audit = json.loads(next(s.detail for s in local.trace if s.step == "calculation_evidence"))
    assert local.answer == 7, local.error
    assert set(audit["queries"][0]["included"]) == EXPECTED
    assert scene.entities[next(i for i,e in enumerate(scene.entities) if e.id == "det-35")].attrs["calculation_excluded"]
    (out/"local-replay.json").write_text(local.model_dump_json(indent=2), encoding="utf-8")
    print("Offline replay: 7 doors, score", local.confidence, "included:", sorted(EXPECTED), flush=True)
    for attempt in range(48):
        try:
            if request("/api/health").get("room_link_validator") == VALIDATION_VERSION:
                break
        except urllib.error.HTTPError as error:
            if error.code not in (404, 502, 503, 504):
                raise
        if attempt % 6 == 0:
            print("Waiting for", VALIDATION_VERSION, flush=True)
        time.sleep(5)
    else:
        raise RuntimeError("Expected deployment not observed; no live question sent")
    answer = request("/api/ask", {"path": PATH, "question": "count the doors in Couloir 4.05"})
    (out/"live-answer.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
    refreshed = request(route)
    (out/"live-scene.json").write_text(json.dumps(refreshed, indent=2), encoding="utf-8")
    live_audit = json.loads(next(s["detail"] for s in answer["trace"] if s["step"] == "calculation_evidence"))
    assert answer["answer"] == 7, answer.get("error")
    assert set(live_audit["queries"][0]["included"]) == EXPECTED
    assert live_audit["threshold"] == .6
    print("Live result:", answer["answer"], "confidence:", answer["confidence"], "cost:", answer["cost_usd"], flush=True)
    print("Excluded legend:", next(e for e in live_audit["queries"][0]["excluded"] if e["id"] == "det-35"), flush=True)
    print("Report:", out.resolve(), flush=True)


if __name__ == "__main__":
    main()
