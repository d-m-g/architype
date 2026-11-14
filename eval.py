#!/usr/bin/env python3
"""Run dataset questions through the pipeline, score them by the dataset README's rules, log every row.

  uv run python eval.py --source swiss_dwellings --limit 200   # seeded sample of the held-out test split
  uv run python eval.py --source cubicasa5k --fresh            # rebuild scenes instead of reading .cache/
  uv run python eval.py --baseline                             # free baseline: most common answer per template

Shared file. Every lane measures itself with this, on the same split, so the numbers are comparable.
"""
import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from blueprints import pipeline
from blueprints.schemas import Answer, EvalRow
from blueprints.scoring import is_correct, split_of


def template(question: str) -> str:
    """The question with quoted labels and numbers masked, so 'room 5.11' and 'room 7.02' are one template."""
    return re.sub(r"'[^']*'|«[^»]*»|\d+(?:[.,]\d+)?", "#", question)


def mode_baseline(train: list[dict]):
    """Always give the most common training answer for the question's template. It costs nothing; beat it."""
    seen: dict[tuple, Counter] = defaultdict(Counter)
    for q in train:
        seen[pipeline.source_of(q["path"]), template(q["question"])][json.dumps(q["answer"]), q["unit"]] += 1

    def ask(path: str, question: str) -> Answer:
        answers = seen.get((pipeline.source_of(path), template(question)))
        if not answers:
            return Answer(error="template not seen in the train split")
        (answer, unit), n = answers.most_common(1)[0]
        return Answer(answer=json.loads(answer), unit=unit, confidence=n / answers.total())

    return ask


def run(questions: list[dict], ask) -> list[EvalRow]:
    rows = []
    # Sorted by path so one drawing's questions run together and its scene is built once.
    for i, q in enumerate(sorted(questions, key=lambda q: q["path"]), 1):
        t0 = time.perf_counter()
        try:
            a = ask(q["path"], q["question"])
        except Exception as e:  # one broken drawing must not stop a long run; it shows up in the error list
            a = Answer(error=f"{type(e).__name__}: {e}")
        gold = q["answer"] if isinstance(q["answer"], (int, float, str)) else json.dumps(q["answer"])
        rows.append(EvalRow(
            path=q["path"], source=pipeline.source_of(q["path"]), question=q["question"], type=q["type"],
            gold=gold, gold_unit=q["unit"], pred=a.answer, pred_unit=a.unit,
            correct=is_correct(a.answer, a.unit, q["answer"], q["unit"], q["type"]),
            confidence=a.confidence, tier=a.tier, cost_usd=a.cost_usd, ms=(time.perf_counter() - t0) * 1000,
            n_evidence=len(a.evidence) + len(a.extra_entities), error=a.error))
        if i % 5000 == 0:
            print(f"  {i}/{len(questions)}", file=sys.stderr)
    return rows


def summarise(rows: list[EvalRow]) -> None:
    groups: dict[tuple, list[EvalRow]] = defaultdict(list)
    for r in rows:
        for key in [("", ""), (r.source, ""), (r.source, r.type)]:
            groups[key].append(r)
    pct = lambda part, whole: f"{100 * part / whole:5.1f}%" if whole else "    - "
    print(f"{'group':28s} {'n':>7s} {'unscored':>8s} {'answered':>8s} {'accuracy':>8s} {'acc if answered':>15s} {'mean conf':>9s} {'usd/1k q':>9s}  tiers")
    for (source, qtype), rs in sorted(groups.items()):
        scored = [r for r in rs if r.correct is not None]
        answered = [r for r in scored if r.pred is not None]
        right = sum(r.correct for r in scored)
        conf = f"{sum(r.confidence for r in answered) / len(answered):9.2f}" if answered else "        -"
        tiers = ", ".join(f"{t} {100 * n / len(rs):.0f}%" for t, n in Counter(r.tier for r in rs).most_common())
        name = "ALL" if not source else f"  {source}" if not qtype else f"    {qtype}"
        print(f"{name:28s} {len(rs):7d} {len(rs) - len(scored):8d} {pct(len(answered), len(scored)):>8s} {pct(right, len(scored)):>8s} "
              f"{pct(right, len(answered)):>15s} {conf} {1000 * sum(r.cost_usd for r in rs) / len(rs):9.3f}  {tiers}")
    errors = Counter(r.error for r in rows if r.error)
    if errors:
        print("\nmost common errors:")
        for message, n in errors.most_common(5):
            print(f"  {n:6d}  {message[:140]}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data", type=Path, default=pipeline.DATA_ROOT, help="dataset folder holding questions.jsonl and data/")
    p.add_argument("--split", choices=["test", "train", "all"], default="test")
    p.add_argument("--source", help="only this source, for example swiss_dwellings")
    p.add_argument("--type", help="only this question type: count, area, length, volume or text")
    p.add_argument("--limit", type=int, help="score a seeded random sample of this many questions")
    p.add_argument("--baseline", action="store_true", help="score the free most-common-answer baseline instead of the pipeline")
    p.add_argument("--vision", action="store_true", help="score the vision reader alone (the route for scans and uploads)")
    p.add_argument("--fresh", action="store_true", help="rebuild scenes instead of reading .cache/scenes")
    p.add_argument("--out", type=Path, help="run log path; default runs/<time>-<what>.jsonl")
    args = p.parse_args()

    pipeline.DATA_ROOT = args.data.resolve()
    pipeline.READ_CACHE = not args.fresh
    everything = [json.loads(line) for line in (pipeline.DATA_ROOT / "questions.jsonl").open(encoding="utf-8")]
    keep = lambda q: (not args.source or pipeline.source_of(q["path"]) == args.source) and (not args.type or q["type"] == args.type)
    questions = [q for q in everything if keep(q) and args.split in ("all", split_of(q["path"]))]
    if args.limit and args.limit < len(questions):
        questions = random.Random(0).sample(questions, args.limit)
    if not questions:
        sys.exit("no questions match these filters")

    ask = mode_baseline([q for q in everything if split_of(q["path"]) == "train"]) if args.baseline else pipeline.answer
    if args.vision:
        from blueprints.brain.escalate import answer_from_vision

        def ask(path, question):
            try:
                return answer_from_vision(question, path)
            except NotImplementedError as e:
                return Answer(error=str(e))
    rows = run(questions, ask)

    what = "-".join(filter(None, ["baseline" if args.baseline else "vision" if args.vision else "pipeline", args.source, args.type]))
    out = args.out or Path("runs") / f"{time.strftime('%Y%m%d-%H%M%S')}-{what}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
    summarise(rows)
    print(f"\n{len(rows)} rows from the {args.split} split written to {out}")


if __name__ == "__main__":
    main()
