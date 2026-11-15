"""Lane C. The expensive path, called when the cheap path gave no answer or a low-confidence one.

1. A drawing with no scene (no builder yet, a hand drawing, an upload) is read once by Gemini (vision.read_scene)
   and the plan runs on that scene with the normal executor.
2. If that still gives no answer, or a builder's answer is low-confidence, the Nemotron agent takes over: it reasons
   over the scene with tools (list entities, run a plan through the executor, ask Gemini about a region) and answers
   with the entities it used. Its tool calls are logged as trace steps, for the answer card.
   On a scene parsed from a DXF, vector PDF or IFC file it has no look tool: the file holds exact geometry, so a size
   is one the parser recorded or it is not answered. No model reads a measurement off the picture of such a file.
"""
import base64
import ast
import json
import math
import os
import time

from ..costs import llm_step
from ..executor import execute
from ..room_queries import ROOM_FILTERS
from ..schemas import Answer, QueryPlan, Scene, TraceStep
from .plan import GEMINI_URL, cached_plan
from .room_link_vision import execute_with_links, resolve, refinement_exhausted

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
- Look at the inventory you are given first: it says what the scene's tables hold and what is NOT recorded.
- Use query for every count, sum, ranking, comparison, filter or lookup: it runs one read-only SQL SELECT on the
  tables entities and links and returns the rows. Never compute or estimate a number yourself.
- For doors/windows/fixtures in a room, join through links (entity_id, room_id): a door between two rooms has two
  rows, so each object counts once per room. Room ids distinguish rooms sharing a label. Never match by label.
  list_entities exposes room connection status; unresolved or ambiguous connections refuse a per-room result.
  Never replace an unresolved per-room count with a count of all doors on the page.
  query automatically attempts bounded vision refinement when room connections prevent a per-room result.
  For room rankings, call resolve_room_connections first if door/window links are unresolved, then query
  again. If connections remain uncertain, explain the limitation; do not invent a definitive winner.
  If refinement reports a budget/cooldown limit or leaves a connection unresolved, do not repeat the same
  refinement or identical query. Only use look again if a specific different crop can resolve the issue.
- Each entity is one room or object. Never combine rooms that share a label unless the question asks for a total.
- A size is a recorded quantity, never your estimate: m2 is area (a room's floor, a slab's top, a wall's side; floor
  area without rooms is slab subtype "floor", never roof slabs), m is the type's one length (a room's perimeter, a
  door's or window's width, a wall's length), m3 is volume; width_m, length_m, height_m and thickness_m are further
  dimensions in metres. NULL means not recorded.
- A room's wall_m2 is its wall area net of doors and windows (unit m2). Wall area, paint, wallpaper and wall-tile
  questions about rooms sum wall_m2 over rooms, not m2 over wall entities.
- Use look only when the scene lacks something you need; it asks a vision model about the page or a region of it.
- Finish by calling answer with the value, its unit, the ids of the entities the answer rests on, and a one-sentence
  explanation. When the question asks how many, how big or how much, the answer is that number and the names go in
  the explanation (e.g. answer 31.05, unit m2, explanation "Reduit 2.30 is the largest room").
- For EVERY final answer that is a number, a ranking or a list, include decision: the JSON that computes the FINAL
  requested result, {"kind": "direct" or "computed", "queries": {name: SQL}, "formula": arithmetic over the query
  names and numeric assumptions, "answer": "value", "top" or "rows", "unit", "assumptions"}, including tile size, waste
  and rounding when requested. Each query returns id, or value with ids = group_concat(id); "top" returns every
  candidate ordered by value, best first, and the server reports ties. The server executes it and uses its result,
  formula and evidence, not your proposed number or the arithmetic in your explanation. Select a single room by its
  number and apartment when appropriate; do not sum every bathroom for a question about the largest bathroom.
- If the question cannot be answered from this drawing, call answer with answer "cannot answer" and say why."""

LOOK_LINE = "- Use look only when the scene lacks something you need; it asks a vision model about the page or a region of it."
PARSED_LINE = """- This scene was parsed from the drawing's own file, which holds exact geometry. Sizes come only from the scene's
  recorded quantities: never estimate a length, area or height, and there is no tool to read one off the picture.
  If the quantity the question needs is not recorded, call answer with "cannot answer" and say which measurement is missing."""
assert LOOK_LINE in SYSTEM


def instructions(scene: Scene) -> tuple[str, list[dict]]:
    """The agent's system prompt and tools for a scene. A parsed file (DXF, vector PDF, IFC) gets no look tool: its
    sizes are the parser's or nobody's. A detected or vision-read image keeps it, the picture being all there is."""
    if scene.tier != "parse":
        return SYSTEM, TOOLS
    return SYSTEM.replace(LOOK_LINE, PARSED_LINE), [t for t in TOOLS if t["function"]["name"] not in ("look", "resolve_room_connections")]  # both ask a vision model


TOOLS = [
    {"type": "function", "function": {
        "name": "resolve_room_connections", "description": "Ask vision to resolve uncertain PNG/JPEG room connections; cached and cost bounded. Re-list entities afterwards.",
        "parameters": {"type": "object", "required": ["type"], "properties": {
            "type": {"type": "string", "enum": ["door", "window", "fixture"]}}}}},
    {"type": "function", "function": {
        "name": "list_entities", "description": "List scene entities, optionally filtered. Returns id, type, subtype, label, number, apartment, level, area_m2, "
                                          "qty (the other recorded quantities by unit: m, m3, ft2, wall_m2), sizes (width_m, length_m, height_m, thickness_m "
                                          "and how they were obtained), conf. A long list is cut short: narrow it with the filters.",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string", "description": "room, apartment, level, door, window, fixture, wall, slab, column, row or other"},
            "subtype": {"type": "string"}, "label": {"type": "string", "description": "case-insensitive substring of the label"},
            "min_area_m2": {"type": "number", "description": "keep only entities with an area of at least this"},
            "max_area_m2": {"type": "number", "description": "keep only entities with an area of at most this"},
            "sort": {"type": "string", "enum": ["area_desc", "area_asc"], "description": "order by area, e.g. for the largest or smallest room"},
            "limit": {"type": "integer", "description": "return at most this many entities"}}}}},
    {"type": "function", "function": {
        "name": "query", "description": "Run one read-only SQL SELECT on the scene's tables (entities, links; see the inventory) and get the rows.",
        "parameters": {"type": "object", "required": ["sql"], "properties": {"sql": {"type": "string", "description":
            "e.g. SELECT id, label, number, m2 FROM entities WHERE type = 'room' ORDER BY m2 DESC LIMIT 5; or SELECT r.id, "
            "count(d.id) AS value, group_concat(d.id) AS ids FROM entities r JOIN links l ON l.room_id = r.id JOIN entities d "
            "ON d.id = l.entity_id AND d.type = 'door' WHERE r.type = 'room' GROUP BY r.id ORDER BY value DESC"}}}}},
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
            "decision": {"type": "string", "description": "Required for a number, ranking or list: the complete decision JSON (queries as SQL, optional formula, answer value|top|rows) computing the final requested result. The server runs it and uses its result as the answer."}}}}},
]


def escalate(question: str, path: str, scene: Scene | None, draft: Answer, history: list | None = None) -> Answer:
    """Return a new Answer with tier="llm", keeping draft.trace. Raise NotImplementedError to keep the draft."""
    if scene is None:
        if path.lower().endswith(".ifc"):  # a model, not a picture: the vision reader cannot read it
            raise NotImplementedError("the IFC model could not be read")
        result = answer_from_vision(question, path, draft, history)
        from .vision import not_a_floor_plan, read_scene
        vision = read_scene(path)[0]
        if result.answer is not None or not_a_floor_plan(vision) or refinement_exhausted(result):
            return result
        return agent(question, vision, result, inline_evidence=True, history=history)
    return agent(question, scene, draft, inline_evidence=False, history=history)


def answer_from_vision(question: str, path: str, draft: Answer | None = None, history: list | None = None) -> Answer:
    """Answer on the Gemini vision scene: for drawings with no builder, and for uploads."""
    draft = draft or Answer()
    from .vision import not_a_floor_plan, read_scene
    vision, fresh = read_scene(path)
    kind = not_a_floor_plan(vision)
    if kind:
        return Answer(tier="llm", error=f"not a floor plan (read as: {kind}); this tool answers questions about floor plans",
                      trace=draft.trace + (vision.trace if fresh else []))
    found, plan_steps = cached_plan(question, vision), []
    if found:  # the free path: a question shape planned before
        plan, plan_steps = found
        result = execute_with_links(plan, vision)
    else:  # any other wording: decided from what the vision scene holds (resolve.py)
        from .resolve import resolve as from_data
        try:
            result = from_data(question, vision, None, history)
        except NotImplementedError as e:  # no model to decide with: escalate() hands it to the agent
            return Answer(tier="llm", error=str(e), trace=draft.trace + getattr(e, "steps", []) + (vision.trace if fresh else []))
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


def _tool(name: str, args: dict, scene: Scene) -> tuple[str, list[TraceStep]]:
    """Run one tool call. Returns the text the agent sees and any costed steps."""
    if name == "resolve_room_connections":
        steps = resolve(scene, args.get("type", "door"))
        return json.dumps({"detail": [s.detail for s in steps], "remaining": sum(
            e.type == args.get("type", "door") and e.attrs.get("room_link_status") in ("partial", "uncertain") for e in scene.entities)}), steps
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
                                   "qty": {u: v for u, v in e.qty.items() if u != "m2"} or None,
                                   # dimensions in metres and where sizes came from; not a level's page frame
                                   "sizes": {k: v for k, v in e.attrs.items() if not k.startswith("frame_") and (
                                       k.endswith("_source") or k.endswith("_m") and type(v) in (int, float))} or None,
                                   "conf": round(e.conf, 2),
                                   "connections": {k: v for k, v in e.attrs.items() if k in (
                                       "room_id", "room_id2", "room", "room2", "room_label", "room_label2",
                                       "room_link_status", "room_link_candidates", "room_link_reason", "room_link_review")} or None
                                   }.items() if v is not None} for e in hits]
        limit = args.get("limit") if isinstance(args.get("limit"), int) and args["limit"] > 0 else 150
        shown = rows[:min(limit, 150)]
        reply = lambda: json.dumps({"count": len(rows), "shown": len(shown), "entities": shown}, ensure_ascii=False)
        while len(reply()) > TOOL_REPLY_CHARS and len(shown) > 1:  # whole rows, not JSON cut off mid-entity
            shown = shown[:len(shown) * 3 // 4]
        return reply(), []
    if name == "query":
        from . import resolve as data
        probe = data.Decision(kind="direct", queries={"q": args.get("sql", "")})
        steps = data.refine_links(probe, scene)  # a per-room result is only as good as the room links
        try:
            rows = data.run(data.table(scene), args.get("sql", ""))
        except data.QueryError as e:
            return f"query failed: {e}", steps
        try:
            note = data._links_usable(probe, scene)
        except data.Abstain as e:
            note = f"NOT USABLE for a per-room answer: {e}"
        shown = rows
        reply = lambda: json.dumps({"rows": len(rows), "shown": len(shown), "result": shown, "note": note}, ensure_ascii=False)
        while len(reply()) > TOOL_REPLY_CHARS and len(shown) > 1:  # whole rows, as list_entities
            shown = shown[:len(shown) * 3 // 4]
        return reply(), steps
    if name == "run_plan":  # no longer offered to the model (query replaces it); kept for callers that hold a QueryPlan
        try:
            plan = QueryPlan.model_validate_json(args.get("plan", ""))
        except ValueError as e:
            return f"invalid plan: {e}", []
        known = {k for e in scene.entities for k in e.attrs} | ROOM_FILTERS.keys()
        unknown = sorted({k for q in plan.queries.values() for k in q.where} - known)
        if unknown:  # an invented filter would silently match nothing and read as a confident zero
            return (f"invalid plan: no entity has the attribute(s) {unknown} for `where`. To filter by area use "
                    f"list_entities with min_area_m2 / max_area_m2."), []
        result = execute_with_links(plan, scene)
        return json.dumps({"answer": result.answer, "unit": result.unit, "formula": result.formula,
                           "evidence": result.evidence[:100], "error": result.error}), [s for s in result.trace if s.step == "resolve_room_connections"]
    if name == "look":
        if scene.tier == "parse":  # not offered for a parsed file; refused if a model calls it anyway
            return ("look is not available: this scene was parsed from the drawing's file, and sizes come only from its "
                    "recorded quantities. If the quantity is not recorded, answer 'cannot answer' and name it."), []
        text, step = _look(scene, args.get("question", ""), args.get("box"))
        return text, [step]
    return f"unknown tool {name}", []


def _verified_decision(text: str, scene: Scene) -> Answer:
    """Bind a final answer to queries run on the scene: the value, formula and evidence of one execution."""
    from . import resolve as data
    try:
        result = data.execute(data.parse(text), scene)
    except data.QueryError as e:
        raise ValueError(str(e)) from None
    if result.answer is None:
        raise ValueError(result.error or "the scene cannot support this result")
    return result


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
    known = {k for e in scene.entities for k in e.attrs} | ROOM_FILTERS.keys()
    unknown = sorted({k for q in plan.queries.values() for k in q.where} - known)
    if unknown:
        raise ValueError(f"no entity has the attribute(s) {unknown} for `where`")
    # Final validation must not hide paid refinement costs in an exception. run_plan owns those calls.
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


TOOL_REPLY_CHARS = 8000  # what the agent is shown of one tool reply
AGENT_FALLBACK_MODEL = "gemini-3.8-flash"  # runs the same loop when Nemotron does not answer in time
CALL_TIMEOUT_S = 40
AGENT_BUDGET_S = 120


class _Unavailable(Exception):
    """The agent's model did not answer in time or errored; the next provider takes over."""


_down_until: dict[str, float] = {}  # model -> monotonic time until which it is skipped after failing
SKIP_AFTER_FAILURE_S = 300  # ponytail: per-process memory; a shared health check if we ever run several workers


def agent(question: str, scene: Scene, draft: Answer, inline_evidence: bool, history: list | None = None) -> Answer:
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
            return _agent_loop(question, scene, draft, inline_evidence, url, key, model, extra, deadline, failures, history)
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


def _agent_loop(question, scene, draft, inline_evidence, url, key, model, extra, deadline, failures, history=None) -> Answer:
    import openai
    from .resolve import HISTORY_RULES, clean_history, history_block, inventory
    history = clean_history(history, scene)  # the same capped, quoted turns the decision call gets
    context = (f"{history_block(history)}Question: {question}\n\nInventory of the scene:\n{inventory(scene)}\n\n"
               f"The cheap path said: {draft.answer!r} (confidence {draft.confidence:.2f}){f'; error: {draft.error}' if draft.error else ''}.")
    system, tools = instructions(scene)
    messages = [{"role": "system", "content": system + (HISTORY_RULES if history else "")}, {"role": "user", "content": context}]
    # A provider that failed first is still on the card: the failed attempt shows as a zero-cost step.
    steps: list[TraceStep] = [TraceStep(step=f"escalate:unavailable {f.split(':')[0]}") for f in failures]
    force_answer = False
    for turn in range(MAX_TURNS):
        remaining = deadline - time.monotonic()
        if remaining < 5:
            raise _Unavailable(f"out of the {AGENT_BUDGET_S} s budget")
        # A reply without a tool call, or the last turn, gets one more chance with the answer tool required.
        choice = {"type": "function", "function": {"name": "answer"}} if force_answer or turn == MAX_TURNS - 1 else "auto"
        client = openai.OpenAI(api_key=key, base_url=url, max_retries=0, timeout=min(CALL_TIMEOUT_S, remaining))
        t0 = time.perf_counter()
        try:
            reply = client.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice=choice,
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
                if value is not None and (numeric or args.get("unit") is not None or args.get("decision")):
                    try:  # decision: SQL, run here. plan: a QueryPlan, from callers that still hold one.
                        verified = _verified_decision(args["decision"], scene) if args.get("decision") else _verified_plan(args.get("plan", ""), scene)
                    except (ValueError, TypeError, ArithmeticError, SyntaxError) as error:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": f"Final answer not verified: {error}. Use query to check (it attempts room-connection refinement if needed), then call answer again with the complete final decision JSON, or answer 'cannot answer'."})
                        force_answer = "per-room count" not in str(error)
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
            text, costed = _tool(call.function.name, args, scene)
            asked = ", ".join(f"{k}={v}" for k, v in args.items())
            steps.append(TraceStep(step=f"tool:{call.function.name}", ms=(time.perf_counter() - t1) * 1000,
                                   detail=f"{asked[:300]} → {text[:400]}"))
            steps += costed
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text[:TOOL_REPLY_CHARS]})
    return Answer(tier="llm", error="Could not verify a final answer against the drawing. No unverified number is shown.",
                  trace=draft.trace + steps)
