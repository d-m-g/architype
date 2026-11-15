"""Benchmark the escalation agent's model on multi-step questions whose answer is in the scene.

Questions are written from Swiss DXF scenes (test split) and their gold answers are computed from the same scene,
so a wrong answer is the agent's reasoning, not missing data.

  uv run --env-file .env --extra brain python -m blueprints.brain.bench_agent --drawings 8
"""
import argparse
import json
import math
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import pipeline
from ..schemas import Answer, Scene
from ..scoring import is_correct, split_of
from . import escalate

VARIANTS = {  # name -> (agent models, checker)
    "nemotron": lambda: ([escalate.nemotron()], None),
    "nemotron+think": lambda: ([escalate.nemotron(think=True)], None),
    "carbon": lambda: ([escalate.gemini("gemini-4-carbon")], None),
    "nemotron->carbon": lambda: ([escalate.nemotron(), escalate.gemini("gemini-4-carbon")], None),
    "think+carbon-check": lambda: ([escalate.nemotron(think=True)], escalate.gemini("gemini-4-carbon")),
}


def questions(scene: Scene) -> list[dict]:
    """Multi-step questions with gold answers computed from the scene. Skips any that do not apply."""
    rooms = [e for e in scene.entities if e.type == "room" and "m2" in e.qty]
    indoor = [e for e in rooms if not e.outdoor]
    by = lambda sub: [e for e in rooms if e.subtype == sub]
    out = []

    def add(q, gold, unit, kind):
        out.append({"question": q, "gold": gold, "unit": unit, "type": kind})

    if by("bedroom"):
        add("What is the area of the largest bedroom?", max(e.qty["m2"] for e in by("bedroom")), "m2", "area")
    if indoor:
        add("How many indoor rooms are larger than 12 m²?", sum(e.qty["m2"] > 12 for e in indoor), "count", "count")
    if len(indoor) >= 2:
        a = [e.qty["m2"] for e in indoor]
        add("How much larger, in m², is the largest indoor room than the smallest indoor room?",
            round(max(a) - min(a), 2), "m2", "area")
    if len(by("bathroom")) >= 2:
        add("What is the average area of a bathroom on this plan?",
            round(statistics.mean(e.qty["m2"] for e in by("bathroom")), 2), "m2", "area")
    wet = by("bathroom") + by("kitchen")
    if wet:
        add("How many 30 × 30 cm floor tiles are needed to tile all bathrooms and kitchens, adding 10% for waste?",
            math.ceil(round(sum(e.qty["m2"] for e in wet), 6) * 1.1 / 0.09), "count", "count")
    apts = defaultdict(float)
    for e in indoor:
        if e.apartment:
            apts[e.apartment] += e.qty["m2"]
    if len(apts) >= 2:
        add("What is the total indoor room area of the apartment with the most indoor room area?",
            round(max(apts.values()), 2), "m2", "area")
    return out


def run_one(variant: str, path: str, q: dict) -> dict:
    scene = pipeline.get_scene(path)
    draft = Answer(error="the planner judged this question unsupported")
    t0 = time.perf_counter()
    try:
        providers, check = VARIANTS[variant]()
        a = escalate.agent(q["question"], scene, draft, inline_evidence=False, providers=providers, check=check)
    except Exception as e:
        a = Answer(error=f"{type(e).__name__}: {e}")
    return {"variant": variant, "path": path, **q, "pred": a.answer, "pred_unit": a.unit,
            "correct": bool(is_correct(a.answer, a.unit, q["gold"], q["unit"], q["type"])),
            "cost_usd": a.cost_usd, "s": time.perf_counter() - t0,
            "turns": sum(s.step == "escalate" for s in a.trace),
            "carbon_turns": sum(s.step == "escalate" and s.model == "gemini-4-carbon" for s in a.trace),
            "models": sorted({s.model for s in a.trace if s.step == "escalate" and s.model}), "error": a.error}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--drawings", type=int, default=8)
    p.add_argument("--variants", default=",".join(VARIANTS))
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    escalate.SKIP_AFTER_FAILURE_S = 0  # one slow call must not make the rest of the run skip that model
    escalate.CALL_TIMEOUT_S, escalate.AGENT_BUDGET_S = 90, 240  # same for every variant; latency is reported

    paths = sorted({json.loads(l)["path"] for l in open(pipeline.DATA_ROOT / "questions.jsonl") if "swiss_dwellings" in l})
    paths = [x for x in paths if split_of(x) == "test"][:args.drawings]
    jobs = [(v, x, q) for x in paths for q in questions(pipeline.get_scene(x)) for v in args.variants.split(",")]
    print(f"{len(jobs)} runs: {len(paths)} drawings, {len(jobs) // len(args.variants.split(','))} questions", flush=True)

    out = Path("runs") / f"bench-agent-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    rows = []
    with ThreadPoolExecutor(args.workers) as pool, out.open("w") as f:
        for r in pool.map(lambda j: run_one(*j), jobs):
            rows.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{'ok ' if r['correct'] else 'BAD'} {r['variant']:17s} {r['pred']!s:>8} vs {r['gold']!s:>8}  "
                  f"{r['s']:5.1f}s  {r['question'][:60]}", flush=True)

    print(f"\n{'variant':18s} {'correct':>8s} {'answered':>9s} {'wrong':>6s} {'$/q':>8s} {'median s':>9s} {'turns':>6s}")
    for v in args.variants.split(","):
        rs = [r for r in rows if r["variant"] == v]
        n = len(rs)
        answered = sum(r["pred"] is not None for r in rs)
        right = sum(r["correct"] for r in rs)
        print(f"{v:18s} {right / n:8.0%} {answered / n:9.0%} {(answered - right) / n:6.0%} "
              f"{sum(r['cost_usd'] for r in rs) / n:8.4f} {statistics.median(r['s'] for r in rs):9.1f} "
              f"{statistics.mean(r['turns'] for r in rs):6.1f}")
    print(f"\nrows: {out}")


if __name__ == "__main__":
    main()
