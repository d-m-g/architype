"""Lane C. The expensive path, called when the cheap path gave no answer or a low-confidence one.

1. A drawing with no scene (no builder yet, a hand drawing, an upload) is read once by Gemini (vision.read_scene)
   and the plan runs on that scene with the normal executor.
2. If that still gives no answer, or a builder's answer is low-confidence, the Nemotron agent takes over: it reasons
   over the scene with tools (list entities, run a plan through the executor, ask Gemini about a region) and answers
   with the entities it used. Its tool calls are logged as trace steps, for the answer card.
"""
import base64
import ast
import json
import math
import os
import time
from collections import Counter

from ..costs import llm_step
from ..executor import execute
from ..schemas import Answer, QueryPlan, Scene, TraceStep
from .plan import GEMINI_URL, plan_with_trace

# ponytail: fixed confidences; replace with per-question-kind accuracy measured on the train split.
VISION_CONFIDENCE = 0.7
AGENT_CONFIDENCE = 0.6

# NVIDIA's sponsored Verda endpoint by default. Backup: NEMOTRON_URL=https://integrate.api.nvidia.com/v1,
# NEMOTRON_KEY_VAR=NVIDIA_API_KEY and NEMOTRON_MODEL=<the id build.nvidia.com shows>; price that id in costs.py first.
NEMOTRON_MODEL = os.environ.get("NEMOTRON_MODEL", "nemotron-3.5-super-vl")
NEMOTRON_URL = os.environ.get("NEMOTRON_URL", "https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1")
NEMOTRON_KEY_VAR = os.environ.get("NEMOTRON_KEY_VAR", "VERDA_INFERENCE_KEY")
LOOK_MODEL = "gemini-3.8-flash"
MAX_TURNS = 8

SYSTEM = """You answer a question about one construction drawing, using tools. The drawing has already been read into
a scene: a list of entities (rooms, doors, windows, fixtures, apartments, levels) with ids, labels and quantities.
- Use list_entities to see what is there, run_plan for general counts/sums/arithmetic and rank_rooms for room connection rankings. Never compute numbers yourself.
- For ranking rooms by connected doors, windows or fixtures, use rank_rooms. It counts recorded connections across
  the full scene, reports ties and refuses incomplete links. Finish with its result_id; the server verifies the
  room ranking and its evidence together. Do not infer ranks from a truncated list_entities response.
- Each entity is one room or object. For "the largest / smallest room" call list_entities with sort and limit 1 and
  report that single entity; never combine rooms that share a label unless the question asks for a total.
- Use look only when the scene lacks something you need; it asks a vision model about the page or a region of it.
- Finish by calling answer with the value, its unit, the ids of the entities the answer rests on, and a one-sentence
  explanation. When the question asks how many, how big or how much, the answer is that number and the names go in
  the explanation (e.g. answer 31.05, unit m2, explanation "Reduit 2.30 is the largest room").
- For numeric final answers other than a verified rank_rooms result_id, include plan: the complete QueryPlan JSON that computes the FINAL requested value,
  including tile size, waste and rounding when requested. The server executes this plan and uses its result, formula
  and evidence, not your proposed number or arithmetic in the explanation. Select a single room by its number and
  apartment when appropriate; do not sum every bathroom for a question about the largest bathroom.
- If the question cannot be answered from this drawing, call answer with answer "cannot answer" and say why."""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_entities", "description": "List scene entities, optionally filtered. Returns id, type, subtype, label, number, apartment, level, area_m2, conf and recorded room connections in attrs.room/room2 (or room_id/room_id2).",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string", "description": "room, apartment, level, door, window, fixture, wall, slab, column, row or other"},
            "subtype": {"type": "string"}, "label": {"type": "string", "description": "case-insensitive substring of the label"},
            "min_area_m2": {"type": "number", "description": "keep only entities with an area of at least this"},
            "max_area_m2": {"type": "number", "description": "keep only entities with an area of at most this"},
            "sort": {"type": "string", "enum": ["area_desc", "area_asc"], "description": "order by area, e.g. for the largest or smallest room"},
            "limit": {"type": "integer", "description": "return at most this many entities"}}}}},
    {"type": "function", "function": {
        "name": "rank_rooms", "description": "Rank rooms by unique connected door, window or fixture counts using all scene records. Reports ties and missing links. Use the returned result_id in the final answer; do not recompute or invent the winner.",
        "parameters": {"type": "object", "properties": {
            "object_type": {"type": "string", "enum": ["door", "window", "fixture"]},
            "order": {"type": "string", "enum": ["asc", "desc"]},
            "apartment": {"type": "string"}, "level": {"type": "string"},
            "page": {"type": "integer", "description": "Zero-based page index"},
            "include_outdoor": {"type": "boolean"},
            "limit": {"type": "integer", "description": "Shown ranking rows (1 to 20). Does not limit the computation or discard ties."}}}}},
    {"type": "function", "function": {
        "name": "run_plan", "description": "Run a QueryPlan on the scene with the exact executor; returns the value, the formula and the evidence ids.",
        "parameters": {"type": "object", "required": ["plan"], "properties": {"plan": {"type": "string", "description":
            'QueryPlan JSON, e.g. {"queries": {"a": {"op": "sum", "type": "room", "subtype": "bathroom", "qty": "m2"}, '
            '"n": {"op": "count", "type": "room", "subtype": "bathroom"}}, "formula": "a / n", "unit": "m2"}. '
            "Query fields: op (count|sum), type, subtype, label, number, level, apartment, outdoor, where, qty (m2|m|m3|ft2, for sum)."}}}}},
    {"type": "function", "function": {
        "name": "look", "description": "Ask a vision model a question about the page image, or about one region of it.",
        "parameters": {"type": "object", "required": ["question"], "properties": {
            "question": {"type": "string"},
            "box": {"type": "array", "items": {"type": "number"}, "description": "[x0, y0, x1, y1] as 0..1 of the page; omit for the whole page"}}}}},
    {"type": "function", "function": {
        "name": "answer", "description": "Give the final answer.",
        "parameters": {"type": "object", "required": ["answer", "explanation"], "properties": {
            "answer": {"type": ["number", "string"]}, "unit": {"type": "string", "enum": ["count", "m2", "m", "m3", "ft2"]},
            "evidence": {"type": "array", "items": {"type": "string"}}, "explanation": {"type": "string"},
            "plan": {"type": "string", "description": "Required for numeric answers unless result_id is supplied: complete QueryPlan JSON computing the final requested value, including all arithmetic and rounding."},
            "result_id": {"type": "string", "description": "Copy the result_id from rank_rooms to use its complete verified answer, room ranking, ties and source evidence."}}}}},
]


def escalate(question: str, path: str, scene: Scene | None, draft: Answer) -> Answer:
    """Return a new Answer with tier="llm", keeping draft.trace. Raise NotImplementedError to keep the draft."""
    if scene is None:
        if path.lower().endswith(".ifc"):  # a model, not a picture: the vision reader cannot read it
            raise NotImplementedError("the IFC model could not be read")
        result = answer_from_vision(question, path, draft)
        from .vision import not_a_floor_plan, read_scene
        vision = read_scene(path)[0]
        if result.answer is not None or not_a_floor_plan(vision) or any(step.step == "room_connections" for step in result.trace):
            return result
        return agent(question, vision, result, inline_evidence=True)
    return agent(question, scene, draft, inline_evidence=False)


def answer_from_vision(question: str, path: str, draft: Answer | None = None) -> Answer:
    """Answer on the Gemini vision scene: for drawings with no builder, and for uploads."""
    draft = draft or Answer()
    from .vision import not_a_floor_plan, read_scene
    vision, fresh = read_scene(path)
    kind = not_a_floor_plan(vision)
    if kind:
        return Answer(tier="llm", error=f"not a floor plan (read as: {kind}); this tool answers questions about floor plans",
                      trace=draft.trace + (vision.trace if fresh else []))
    from ..room_connections import direct_room_ranking
    ranking = direct_room_ranking(question, vision)
    if ranking is not None:
        return ranking.model_copy(update={"tier": "llm", "extra_entities": [e for e in vision.entities if e.id in set(ranking.evidence)],
                                          "trace": draft.trace + (vision.trace if fresh else []) + ranking.trace})
    try:
        plan, plan_steps = plan_with_trace(question, vision)
    except NotImplementedError as e:  # no plan fits (or no planner): escalate() hands it to the agent
        return Answer(tier="llm", error=str(e), trace=draft.trace + getattr(e, "steps", []) + (vision.trace if fresh else []))
    result = execute(plan, vision)
    hits = set(result.evidence)
    return result.model_copy(update={
        "tier": "llm",
        "confidence": VISION_CONFIDENCE if result.answer is not None else 0.0,
        "confidence_note": f"A fixed {VISION_CONFIDENCE:.0%} for answers on the vision model's reading of the drawing; "
                           f"the model gives no per-item score." if result.answer is not None else None,
        "extra_entities": [e for e in vision.entities if e.id in hits],  # the viewer has no scene to look these up in
        "trace": draft.trace + plan_steps + (vision.trace if fresh else []) + result.trace,
    })


def _page_file(scene: Scene):
    from .. import pipeline
    from .vision import drawing_file
    image = scene.pages[0].image
    return pipeline.CACHE / image if image.startswith("pages/") else drawing_file(image)


def _look(scene: Scene, question: str, box: list | None) -> tuple[str, TraceStep]:
    import openai
    import pymupdf
    page = pymupdf.open(_page_file(scene))[0]
    clip = None
    if box and len(box) == 4:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
        r = page.rect
        clip = pymupdf.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height, r.x0 + x1 * r.width, r.y0 + y1 * r.height)
    scale = 2048 / max((clip or page.rect).width, (clip or page.rect).height)
    png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip).tobytes("png")
    client = openai.OpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url=GEMINI_URL, max_retries=3, timeout=120)
    t0 = time.perf_counter()
    reply = client.chat.completions.create(model=LOOK_MODEL, messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        {"type": "text", "text": question + "\nAnswer briefly and say if you are unsure."}]}])
    step = llm_step("look", LOOK_MODEL, reply.usage.prompt_tokens, reply.usage.completion_tokens, (time.perf_counter() - t0) * 1000)
    return reply.choices[0].message.content or "", step


def _tool(name: str, args: dict, scene: Scene, results: dict | None = None) -> tuple[str, list[TraceStep]]:
    """Run one tool call. Returns the text the agent sees and any costed steps."""
    if name == "list_entities":
        lo, hi = args.get("min_area_m2"), args.get("max_area_m2")
        want = lambda e: ((not args.get("type") or e.type == args["type"]) and (not args.get("subtype") or e.subtype == args["subtype"])
                          and (not args.get("label") or args["label"].casefold() in (e.label or "").casefold())
                          and (lo is None or ("m2" in e.qty and e.qty["m2"] >= lo))
                          and (hi is None or ("m2" in e.qty and e.qty["m2"] <= hi)))
        hits = [e for e in scene.entities if want(e)]
        if args.get("sort") in ("area_desc", "area_asc"):  # entities without an area go last either way
            hits = sorted(hits, key=lambda e: (("m2" not in e.qty), -e.qty.get("m2", 0) if args["sort"] == "area_desc" else e.qty.get("m2", 0)))
        rows = [{k: v for k, v in {"id": e.id, "type": e.type, "subtype": e.subtype, "label": e.label, "number": e.number,
                                   "apartment": e.apartment, "level": e.level, "area_m2": e.qty.get("m2"),
                                   "conf": round(e.conf, 2),
                                   "attrs": {k: v for k, v in e.attrs.items() if k in ("room", "room2", "room_label", "room_label2", "room_id", "room_id2", "copies")} or None}.items() if v is not None} for e in hits]
        limit = args.get("limit") if isinstance(args.get("limit"), int) and args["limit"] > 0 else 150
        return json.dumps({"count": len(rows), "entities": rows[:min(limit, 150)]}, ensure_ascii=False), []
    if name == "rank_rooms":
        from ..room_connections import rank_rooms, ranking_id
        arguments = {k: v for k, v in args.items() if k != "limit"}
        try:
            ranked = rank_rooms(scene, **arguments)
            result_id = ranking_id(scene, arguments)
            text = json.dumps(ranked.tool_result(result_id, args.get("limit", 5)), ensure_ascii=False)
        except (ValueError, TypeError) as error:
            return f"invalid room ranking: {error}", []
        if results is not None:
            results[result_id] = ranked.answer
        return text, []
    if name == "run_plan":
        try:
            plan = QueryPlan.model_validate_json(args.get("plan", ""))
        except ValueError as e:
            return f"invalid plan: {e}", []
        known = {k for e in scene.entities for k in e.attrs}
        unknown = sorted({k for q in plan.queries.values() for k in q.where} - known)
        if unknown:  # an invented filter would silently match nothing and read as a confident zero
            return (f"invalid plan: no entity has the attribute(s) {unknown} for `where`. To filter by area use "
                    f"list_entities with min_area_m2 / max_area_m2."), []
        result = execute(plan, scene)
        return json.dumps({"answer": result.answer, "unit": result.unit, "formula": result.formula,
                           "evidence": result.evidence[:100], "error": result.error}), []
    if name == "look":
        text, step = _look(scene, args.get("question", ""), args.get("box"))
        return text, [step]
    return f"unknown tool {name}", []


def _verified_plan(text: str, scene: Scene) -> Answer:
    """Bind a numeric final answer to scene queries and the exact arithmetic executor."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("include plan with the complete final QueryPlan JSON")
    plan = QueryPlan.model_validate_json(text)
    if not plan.queries:
        raise ValueError("the final calculation needs at least one scene query")
    if plan.formula:
        if len(plan.formula) > 200:
            raise ValueError("formula too long")
        names = {node.id for node in ast.walk(ast.parse(plan.formula, mode="eval")) if isinstance(node, ast.Name)}
        if not names.intersection(plan.queries):
            raise ValueError("the final formula must use a scene query")
    known = {k for e in scene.entities for k in e.attrs}
    unknown = sorted({k for q in plan.queries.values() for k in q.where} - known)
    if unknown:
        raise ValueError(f"no entity has the attribute(s) {unknown} for `where`")
    result = execute(plan, scene)
    if result.answer is None:
        raise ValueError(result.error or "the scene cannot support this calculation")
    if not math.isfinite(float(result.answer)):
        raise ValueError("the calculation did not produce a finite number")
    return result


def _calculation_sources(result: Answer, scene: Scene) -> str:
    """Generate the caption from the verified evidence, without repeating model-written arithmetic."""
    evidence = set(result.evidence)
    hits = [e for e in scene.entities if e.id in evidence]
    if not hits:
        return "Calculated from the covered scene; no matching objects were found."

    def label(entity):
        return " · ".join(str(part) for part in (entity.label or entity.type, entity.number,
                                                   f"apartment {entity.apartment}" if entity.apartment else None) if part)

    names = "; ".join(label(e) for e in hits[:6])
    more = f"; and {len(hits) - 6} more objects" if len(hits) > 6 else ""
    return f"Calculated from {names}{more}. See the formula and highlighted source evidence."


AGENT_FALLBACK_MODEL = "gemini-3.8-flash"  # runs the same loop when Nemotron does not answer in time
CALL_TIMEOUT_S = 40
AGENT_BUDGET_S = 120


class _Unavailable(Exception):
    """The agent's model did not answer in time or errored; the next provider takes over."""


_down_until: dict[str, float] = {}  # model -> monotonic time until which it is skipped after failing
SKIP_AFTER_FAILURE_S = 300  # ponytail: per-process memory; a shared health check if we ever run several workers


def agent(question: str, scene: Scene, draft: Answer, inline_evidence: bool) -> Answer:
    """Nemotron with scene tools, falling back to Gemini with the same tools if Nemotron does not respond.
    inline_evidence: return evidence as full entities (the viewer has no scene to look up)."""
    providers = [(NEMOTRON_URL, os.environ.get(NEMOTRON_KEY_VAR), NEMOTRON_MODEL,
                  {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}),
                 (GEMINI_URL, os.environ.get("GEMINI_API_KEY"), AGENT_FALLBACK_MODEL, {})]
    deadline = time.monotonic() + AGENT_BUDGET_S
    failures = []
    for url, key, model, extra in providers:
        if not key:
            failures.append(f"{model}: no key")
            continue
        if time.monotonic() < _down_until.get(model, 0):
            failures.append(f"{model}: failed recently, skipped")
            continue
        try:
            return _agent_loop(question, scene, draft, inline_evidence, url, key, model, extra, deadline, failures)
        except _Unavailable as e:
            _down_until[model] = time.monotonic() + SKIP_AFTER_FAILURE_S
            failures.append(f"{model}: {e}")
    raise NotImplementedError("lane C: no agent model answered (" + "; ".join(failures) + ")")


def _same_value(a, b) -> bool:
    """The same number (an agent may return "124" for 124), or the same text."""
    if a is None or b is None:
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip().casefold() == str(b).strip().casefold()


def _agent_loop(question, scene, draft, inline_evidence, url, key, model, extra, deadline, failures) -> Answer:
    import openai
    kinds = Counter(f"{e.type}:{e.subtype}" if e.subtype else e.type for e in scene.entities)
    context = (f"Question: {question}\n\nScene from {scene.builder}: {dict(kinds)}.\n"
               f"The cheap path said: {draft.answer!r} (confidence {draft.confidence:.2f}){f'; error: {draft.error}' if draft.error else ''}.")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": context}]
    # A provider that failed first is still on the card: the failed attempt shows as a zero-cost step.
    steps: list[TraceStep] = [TraceStep(step=f"escalate:unavailable {f.split(':')[0]}") for f in failures]
    force_answer = False
    verified_results = {}
    for turn in range(MAX_TURNS):
        remaining = deadline - time.monotonic()
        if remaining < 5:
            raise _Unavailable(f"out of the {AGENT_BUDGET_S} s budget")
        # A reply without a tool call, or the last turn, gets one more chance with the answer tool required.
        choice = {"type": "function", "function": {"name": "answer"}} if force_answer or turn == MAX_TURNS - 1 else "auto"
        client = openai.OpenAI(api_key=key, base_url=url, max_retries=0, timeout=min(CALL_TIMEOUT_S, remaining))
        t0 = time.perf_counter()
        try:
            reply = client.chat.completions.create(model=model, messages=messages, tools=TOOLS, tool_choice=choice,
                                                   max_tokens=4096, **extra)
        except openai.OpenAIError as e:
            raise _Unavailable(type(e).__name__) from None
        steps.append(llm_step("escalate", model, reply.usage.prompt_tokens, reply.usage.completion_tokens,
                              (time.perf_counter() - t0) * 1000))
        msg = reply.choices[0].message
        if msg.content:  # the model's own words between tool calls, when it gives any
            steps[-1].detail = msg.content.strip()[:400]
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            messages.append({"role": "user", "content": "Call answer now with your result, its unit, the evidence ids and a one-sentence explanation."})
            force_answer = True
            continue
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if call.function.name == "answer":
                if args.get("result_id"):
                    verified = verified_results.get(args["result_id"])
                    if verified is None:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": "Unknown result_id. Call rank_rooms first and use its returned result_id."})
                        force_answer = False
                        continue
                    ids = set(verified.evidence)
                    return verified.model_copy(update={"tier": "llm", "trace": draft.trace + steps + verified.trace,
                        "extra_entities": [e for e in scene.entities if e.id in ids] if inline_evidence else []})
                ids = {e.id for e in scene.entities}
                evidence = [i for i in args.get("evidence", []) if i in ids]
                value = args.get("answer")
                if isinstance(value, str) and value.strip().lower() == "cannot answer":
                    value = None
                numeric = isinstance(value, (int, float))
                if isinstance(value, str):
                    try:
                        float(value)
                        numeric = True
                    except ValueError:
                        pass
                if value is not None and (numeric or args.get("unit") is not None):
                    try:
                        verified = _verified_plan(args.get("plan", ""), scene)
                    except (ValueError, TypeError, ArithmeticError, SyntaxError) as error:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": f"Final answer not verified: {error}. Call answer again with plan containing the complete final QueryPlan JSON, or answer 'cannot answer'."})
                        force_answer = True
                        continue
                    return verified.model_copy(update={
                        "tier": "llm",
                        "confidence_note": ("Source-derived score, not an overall probability of correctness. "
                                            "Arithmetic verified by the executor; question interpretation is not independently verified. "
                                            + (verified.confidence_note or "")),
                        "interpretation": _calculation_sources(verified, scene),
                        "extra_entities": [e for e in scene.entities if e.id in set(verified.evidence)] if inline_evidence else [],
                        "trace": draft.trace + steps + verified.trace + [TraceStep(step="verify_answer",
                            detail="Final value, formula and evidence come from the same execution; confidence is source-derived.")],
                    })
                note = (f"A fixed {AGENT_CONFIDENCE:.0%} for answers the agent reasons out; it is not derived from "
                        f"this drawing.") if value is not None else None
                if value is not None and _same_value(value, draft.answer):
                    note += f" It agrees with the first route's answer ({draft.confidence:.0%})."
                if _same_value(value, draft.answer) and draft.evidence:
                    # The agent confirmed the cheap path's number: keep its full evidence. Models trim long id
                    # lists (99 of 124 doors cited), which left counted items marked "not cited" in the viewer.
                    evidence = draft.evidence + [i for i in evidence if i not in set(draft.evidence)]
                return Answer(
                    answer=value, unit=args.get("unit") if value is not None else None,
                    confidence=AGENT_CONFIDENCE if value is not None else 0.0, confidence_note=note, tier="llm", evidence=evidence,
                    extra_entities=[e for e in scene.entities if e.id in set(evidence)] if inline_evidence else [],
                    interpretation=args.get("explanation"), error=None if value is not None else args.get("explanation"),
                    trace=draft.trace + steps)
            t1 = time.perf_counter()
            text, costed = _tool(call.function.name, args, scene, verified_results)
            asked = ", ".join(f"{k}={v}" for k, v in args.items())
            steps.append(TraceStep(step=f"tool:{call.function.name}", ms=(time.perf_counter() - t1) * 1000,
                                   detail=f"{asked[:300]} → {text[:400]}"))
            steps += costed
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text[:8000]})
    return Answer(tier="llm", error="Could not verify a final answer against the drawing. No unverified number is shown.",
                  trace=draft.trace + steps)
