#!/usr/bin/env python3
"""Prepare the guest demo: drawings from different sources, each with 3 questions the pipeline answers right without
a model call, so a visitor without the password replays them from the caches. Writes blueprints/guest_cases.json.

  uv run --env-file .env --extra brain --extra parsers --extra detector python tools/guest_cases.py   # pick and write
  uv run --no-sync python tools/guest_cases.py --warm   # on the server: fill its caches from the file, report differences
"""
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from blueprints import pipeline  # noqa: E402
from blueprints.scoring import is_correct  # noqa: E402

OUT = Path(__file__).parent.parent / "blueprints" / "guest_cases.json"
RUNS = Path(__file__).parent.parent / "frontend-d" / "runs"
THUMBS = Path(__file__).parent.parent / "frontend-d" / "guest"
SCENE_MAX_BYTES = 1_000_000  # a larger scene (a whole apartment block's model) opens too slowly for a first visit
MIX = {"cubicasa5k": 8, "swiss_dwellings": 7, "ifc_models": 5}  # the sources whose answers need no agent
PER_DRAWING, TRIES = 3, 12  # questions kept per drawing; questions tried before giving up on it
MIN_ROOMS = 5  # outlined on the page a visitor first sees: fewer is a roof, a basement or a structural model
# Half the brochure scans are sideways or upside down, which nothing here detects. These were looked at with the
# reader's finds drawn on top: upright, and every room outlined. The first MIX of them that pass are offered.
BY_EYE = {"cubicasa5k": [f"data/cubicasa5k/{name}.png" for name in (
    "high_quality_architectural-2561", "high_quality_architectural-12617", "high_quality-6128",
    "high_quality_architectural-14946", "high_quality_architectural-13579", "high_quality_architectural-10212",
    "high_quality_architectural-13791", "high_quality_architectural-8775", "high_quality_architectural-8741",
    "colorful-10315")]}
SKIP = {  # looked at and left out: the answers are right, the first look is poor
    "tuwien-escape-route_real-world-escape-route-test-model-zdb",  # its spaces are drawn apart from its walls
}
KIND = {"cubicasa5k": "Brochure plan, image only", "swiss_dwellings": "CAD plan, read from its vector data"}


def template(question: str) -> str:
    return re.sub(r"'[^']*'|\d+(?:[.,]\d+)?", "#", question)


def read_again_if_old(path: str) -> None:
    """A brochure plan cached before the room segmenter has boxes for six room types and nothing for the living room,
    the hall or the storage: read it again with the current detector and segmenter (two GPU calls, no language model)."""
    from blueprints.detector import rooms
    if pipeline.source_of(path) != "cubicasa5k" or rooms._outlined(pipeline.get_scene(path)):
        return
    token = pipeline.FRESH.set(True)
    try:
        pipeline.get_scene(path)
    finally:
        pipeline.FRESH.reset(token)
        pipeline.get_scene.cache_clear()  # the old scene is still held for the cached route
    if not rooms._outlined(pipeline.get_scene(path)):
        raise RuntimeError("the room segmenter did not outline this plan")


def as_guest(path: str, question: str):
    token = pipeline.GUEST.set(True)
    try:
        return pipeline.answer(path, question)
    finally:
        pipeline.GUEST.reset(token)


def named(path: str) -> tuple[str, str]:
    stem, source = Path(path).stem, pipeline.source_of(path)
    if source == "cubicasa5k":
        return f"Home {stem.rsplit('-', 1)[-1]}", KIND[source]
    if source == "swiss_dwellings":
        return f"Apartment floor {stem.rsplit('floor', 1)[-1]}", KIND[source]
    name = stem.split("_")[0].split("-", 1)[-1].replace("-", " ").title()  # "kit-smiley-west_..." -> "Smiley West"
    return name, "BIM model (IFC), with a 3D view"


def sheet_page(path: str) -> int:
    """The page a drawing opens on: a BIM plan sheet <model>_plan-<n>-<level>.pdf shows storey n of its model's pages."""
    sheet = re.search(r"_plan-(\d+)", path)
    return min(int(sheet.group(1)) - 1, len(pipeline.get_scene(path).pages) - 1) if sheet else 0


def rooms_shown(path: str) -> int:
    page = sheet_page(path)
    return sum(e.type == "room" and e.page == page and len(e.geom) >= 3 for e in pipeline.get_scene(path).entities)


def thumbnail(path: str) -> str:
    """A small JPEG of the drawing for the guest gallery, in frontend-d/guest/: the originals run to megabytes."""
    import hashlib

    from PIL import Image
    image = pipeline.get_scene(path).pages[sheet_page(path)].image
    picture = Image.open(pipeline.CACHE / image if image.startswith("pages/") else pipeline.dataset_file(image)).convert("RGBA")
    flat = Image.new("RGB", picture.size, "white")
    flat.paste(picture, mask=picture.getchannel("A"))
    flat.thumbnail((560, 560))
    name = f"guest/{hashlib.md5(path.encode()).hexdigest()[:12]}.jpg"
    THUMBS.mkdir(exist_ok=True)
    flat.save(THUMBS.parent / name, quality=80, optimize=True)
    return name


def pick() -> None:
    questions = defaultdict(list)
    for line in (pipeline.DATA_ROOT / "questions.jsonl").open(encoding="utf-8"):
        q = json.loads(line)
        if q["question"].startswith(("How", "What", "Which")):  # the English wording; cubicasa5k repeats it in Finnish
            questions[q["path"]].append(q)
    runs = sorted(RUNS.glob("*-pipeline.jsonl"))
    old = {json.loads(line)["path"] for line in runs[-1].open(encoding="utf-8")} if runs else set()  # shown before
    for old_thumb in THUMBS.glob("*.jpg"):
        old_thumb.unlink()  # the set is replaced whole
    cases = []
    for source, wanted in MIX.items():
        paths = sorted(p for p in questions if pipeline.source_of(p) == source and p not in old)
        random.Random(f"2026:{source}").shuffle(paths)  # seeded per source and per drawing: SKIP only removes entries
        models, found = set(), 0
        for path in BY_EYE.get(source, paths):
            model = Path(path).stem.split("_plan-")[0]  # one sheet or model per building
            if found == wanted:
                break
            if model in models or model in SKIP:
                continue
            t0, kept, rows = time.perf_counter(), [], questions[path][:]
            random.Random(path).shuffle(rows)
            for q in rows[:TRIES]:
                if len(kept) == PER_DRAWING:
                    break
                if template(q["question"]) in {template(k["question"]) for k in kept} or not q["answer"]:
                    continue  # one question per wording, and none whose answer is nothing to look at
                try:
                    read_again_if_old(path)
                    a = as_guest(path, q["question"])
                except Exception as e:  # a drawing whose builder fails is simply not offered
                    print(f"  skip {path}: {type(e).__name__}: {e}", file=sys.stderr)
                    break
                # Above the escalation threshold, so the signed-in run that fills the server's caches stops here too.
                if (a.evidence and a.tier != "llm" and a.confidence >= pipeline.ESCALATE_BELOW
                        and is_correct(a.answer, a.unit, q["answer"], q["unit"], q["type"])):
                    kept.append({"question": q["question"], "answer": a.answer, "unit": a.unit})
            fits = (len(kept) == PER_DRAWING and pipeline.scene_file(path).stat().st_size <= SCENE_MAX_BYTES
                    and rooms_shown(path) >= MIN_ROOMS)
            print(f"{'ok  ' if fits else 'drop'} {path} ({time.perf_counter() - t0:.1f}s)", file=sys.stderr)
            if fits:
                name, kind = named(path)
                cases.append({"path": path, "thumb": thumbnail(path), "name": name, "kind": kind, "questions": kept})
                models.add(model)
                found += 1
    OUT.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cases)} drawings written to {OUT}")


def warm() -> None:
    """Answer every prepared question signed in (builds the scene, plans what has no plan yet), then as a guest."""
    wrong = 0
    for case in json.loads(OUT.read_text(encoding="utf-8")):
        path = case["path"]
        read_again_if_old(path)
        if pipeline.source_of(path) == "ifc_models":
            from blueprints.parsers import ifc
            ifc.model_glb(path, pipeline.dataset_file(path))  # the 3D view's model, built on first request otherwise
        for q in case["questions"]:
            full, guest = pipeline.answer(path, q["question"]), as_guest(path, q["question"])
            same = full.answer == guest.answer == q["answer"] and guest.tier != "llm"
            wrong += not same
            print(f"{'ok  ' if same else 'DIFF'} {path} | {q['question'][:70]} -> {guest.answer} (expected {q['answer']}; {full.error or guest.error or ''})")
    sys.exit(f"{wrong} prepared questions do not replay for a guest" if wrong else 0)


if __name__ == "__main__":
    warm() if "--warm" in sys.argv else pick()
