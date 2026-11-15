"""One cached vision look at detections under the evidence cutoff: is each boxed symbol really what was asked?

The executor refuses a raster count while candidates below the cutoff are unresolved. A clear yes counts the symbol
at the fixed vision score, a clear no excludes it, anything else leaves it unresolved, so the refusal stands."""
import base64
import hashlib
import io
import json
import os
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import calculation_evidence as policy
from .. import pipeline
from ..costs import llm_step
from ..schemas import TraceStep
from .plan import GEMINI_URL
from .vision import VISION_MODEL, drawing_file

VERSION = "candidate-review@1"
CONFIRMED_CONF = 0.7  # the fixed score vision answers get (escalate.VISION_CONFIDENCE); the model gives no per-item score
MAX_OBJECTS = 24


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    object_id: str
    verdict: Literal["yes", "no", "unsure"]
    reason: str = Field("", max_length=300)


def _directory(scene):
    # Only extraction inputs, never the scores this review changes.
    geometry = [(e.id, e.attrs.get("detection_original_type", e.type), e.subtype, e.page, e.geom)
                for e in scene.entities if e.type in policy.OBJECT_TYPES]
    key = hashlib.sha256(json.dumps([VERSION, VISION_MODEL, scene.path, geometry], sort_keys=True).encode()).hexdigest()
    return pipeline.CACHE/"candidate-review"/key


def _file(directory, entity):
    return directory/(hashlib.sha256(entity.id.encode()).hexdigest()+".json")


def _box(e):
    return [min(p[0] for p in e.geom), min(p[1] for p in e.geom), max(p[0] for p in e.geom), max(p[1] for p in e.geom)]


def _name(e):
    return f"{e.subtype} ({e.type})" if e.subtype else e.type


def _apply(entity, verdict):
    if verdict.verdict == "yes":
        entity.attrs.setdefault("detector_conf", entity.conf)
        entity.conf = max(entity.conf, CONFIRMED_CONF)
        entity.attrs.update(calculation_confirmed="vision", calculation_review=f"Vision model: {verdict.reason}")
    elif verdict.verdict == "no":
        entity.attrs.update(calculation_excluded=True, calculation_exclusion_reason=f"Vision model: not a {_name(entity)}. {verdict.reason}".strip())
    return verdict.verdict != "unsure"


def apply_cached(scene):
    """Re-apply earlier verdicts; no model call."""
    if not scene.path.lower().endswith(pipeline.IMAGES) or not pipeline.use_cache():
        return 0
    directory, changed = _directory(scene), 0
    if not directory.exists():
        return 0
    for e in scene.entities:
        cached = _file(directory, e)
        if e.type in policy.OBJECT_TYPES and "calculation_review" not in e.attrs and not e.attrs.get("calculation_excluded") and cached.exists():
            try:
                changed += int(_apply(e, Verdict.model_validate_json(cached.read_text(encoding="utf-8"))))
            except ValueError:
                pass  # an invalid cache is not evidence
    return changed


def candidates(plan, scene):
    """The below-cutoff detections that hold a raster count back: what the executor's coverage counts against it."""
    from ..executor import select
    if not policy.raster(scene):
        return []
    seen, out = set(), []
    for q in plan.queries.values():
        if q.op != "count" or q.type not in policy.OBJECT_TYPES:
            continue
        for e in select(q, scene, apply_policy=False):
            if e.id not in seen and e.geom and not e.attrs.get("calculation_excluded") and e.conf < policy.THRESHOLD:
                seen.add(e.id)
                out.append(e)
    return out


def _request(scene, pending):
    import openai
    from PIL import Image, ImageDraw
    llm_step("review_candidates", VISION_MODEL, 0, 0, 0)  # reject an unpriced model before spending
    with Image.open(drawing_file(scene.path)) as opened:
        image = opened.convert("RGB")
        image.thumbnail((2048, 2048))
    raw = io.BytesIO()
    image.save(raw, format="PNG")
    draw = ImageDraw.Draw(image)
    for e in pending:
        x0, y0, x1, y1 = _box(e)
        draw.rectangle([x0*image.width, y0*image.height, x1*image.width, y1*image.height], outline="magenta", width=3)
        draw.text((x0*image.width, y0*image.height-12), e.id, fill="black", stroke_width=1, stroke_fill="magenta")
    marked = io.BytesIO()
    image.save(marked, format="PNG")
    objects = [{"id": e.id, "detected_as": _name(e), "box_xyxy": [round(v, 4) for v in _box(e)]} for e in pending]
    prompt = ("A symbol detector found these objects in this floor plan but was unsure of each. The first image is the "
              "original; the second has a magenta box with the id at each object. For EACH object decide whether the boxed "
              "symbol really is what it was detected as, drawn as part of the plan. A legend sample, text, a different "
              "object or an empty area is 'no'. Use 'unsure' when the drawing does not make it clear. Never count.\n"
              'Return JSON: {"items":[{"object_id":"det-1","verdict":"yes|no|unsure","reason":"brief visible evidence"}]}\n'
              + json.dumps({"objects": objects}))
    client = openai.OpenAI(api_key=pipeline.model_key(), base_url=GEMINI_URL, max_retries=0, timeout=60)
    t = time.perf_counter()
    response = client.chat.completions.create(model=VISION_MODEL, temperature=0, max_tokens=4096, reasoning_effort="low",
        response_format={"type": "json_object"}, messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+base64.b64encode(raw.getvalue()).decode()}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+base64.b64encode(marked.getvalue()).decode()}},
            {"type": "text", "text": prompt}]}])
    usage = response.usage
    output_tokens = max(usage.completion_tokens, (getattr(usage, "total_tokens", 0) or 0)-usage.prompt_tokens)
    step = llm_step("review_candidates", VISION_MODEL, usage.prompt_tokens, output_tokens, (time.perf_counter()-t)*1000)
    verdicts = []
    try:
        for row in json.loads(response.choices[0].message.content or "").get("items", []):
            try:
                verdicts.append(Verdict.model_validate(row))
            except ValueError:
                pass  # one bad record cannot discard the others
    except (ValueError, TypeError, AttributeError):
        pass
    return verdicts, step


def review(plan, scene):
    """At most one model call per question; verdicts are cached per drawing, so a repeat costs nothing."""
    if not scene.path.lower().endswith(pipeline.IMAGES) or len(scene.pages) != 1:
        return []
    reused = apply_cached(scene)
    directory = _directory(scene)
    pending = [e for e in candidates(plan, scene) if not (pipeline.use_cache() and _file(directory, e).exists())][:MAX_OBJECTS]
    if not pending or not pipeline.model_key():
        return [TraceStep(step="review_candidates", cached=True, detail=f"Reused {reused} earlier verdicts; no new model call.")] if reused else []
    try:
        verdicts, step = _request(scene, pending)
    except Exception as error:  # ponytail: a failed call is retried on the next question; add a cooldown if it repeats
        return [TraceStep(step="review_candidates", detail=f"Candidate review unavailable ({type(error).__name__}); the refusal stands.")]
    by_id = {}
    for v in verdicts:
        by_id[v.object_id] = None if v.object_id in by_id else v  # a duplicated id decides nothing
    decided = {"yes": 0, "no": 0, "unsure": 0}
    for e in pending:
        v = by_id.get(e.id) or Verdict(object_id=e.id, verdict="unsure", reason="No unique model verdict.")
        _apply(e, v)
        decided[v.verdict] += 1
        directory.mkdir(parents=True, exist_ok=True)
        _file(directory, e).write_text(v.model_dump_json(), encoding="utf-8")
    step.detail = (f"Looked at {len(pending)} candidates under the {policy.THRESHOLD:.0%} cutoff: {decided['yes']} confirmed, "
                   f"{decided['no']} rejected, {decided['unsure']} unclear. Model-judged, not independently verified.")
    return [step]
