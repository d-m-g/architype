"""A question names something by a word the drawing does not use: one small Gemini call picks the drawing's own.

A plan filters by exact text ({"label": "bathrom 1"}). When nothing of the queried type has that value, a sum has
nothing to add and the question went to the agent (about $0.01 and 30 s), and a count read as a confident zero.
rename() gives the model the asked value and the values the drawing does have, restricted to those or NONE, and
returns the plan with the drawing's value in its place. The pipeline runs that plan through the executor, so the
number, the formula and the evidence are still the executor's; capped() marks the answer as resting on that reading.

Check:  uv run --extra brain python -m blueprints.brain.aliases
"""
import hashlib
import json
import os
import time

from .. import pipeline
from ..costs import llm_step
from ..schemas import Answer, QueryPlan, Scene, TraceStep
from .plan import GEMINI_MODEL, GEMINI_URL

FIELDS = ("label", "number", "level", "apartment")
CONFIDENCE = 0.8  # ponytail: a fixed cap for an answer that rests on a model's reading of a name; measure it in eval
MAX_VALUES = 400  # the drawing's names go into one prompt; a model with more distinct names than this is left to the agent
NONE = "NONE"
PROMPT = """A question about a construction drawing names something by a text the drawing does not use. From the
drawing's own values, pick the one that names the same thing: a spelling variant or typo, an abbreviation, a shorter
or longer form of the same name, or the same word in another language. Answer NONE when no value clearly names the same
thing, or when more than one could. Never pick something merely similar or related: a kitchen is not a dining room,
"apartment 1" is not apartment "A", room 7.1 is not room 7.10.
Reply as JSON: {"match": "<one of the values, copied exactly, or NONE>"}."""


def unknown(plan: QueryPlan, scene: Scene) -> list[tuple[str, str, str, list[str]]]:
    """(query name, field, asked value, the drawing's values) for each text filter whose value nothing of the queried
    type carries. Empty for a plan whose filters all name things the drawing has: then no model is called."""
    out = []
    for name, q in plan.queries.items():
        mine = [e for e in scene.entities if e.type == q.type]
        for field in FIELDS:
            asked = getattr(q, field)
            if asked is None:
                continue
            values = sorted({v for e in mine if isinstance(v := getattr(e, field), str) and v.strip()})
            also = {e.attrs["long_name"] for e in mine if isinstance(e.attrs.get("long_name"), str)} if field == "label" else set()
            if values and asked.strip().casefold() not in {v.strip().casefold() for v in (*values, *also)}:
                out.append((name, field, asked, values))
    return out


def _ask(kind: str, field: str, asked: str, values: list[str]) -> tuple[str | None, list[TraceStep]]:
    """The drawing's value for `asked`, or None; cached per question of this kind, so a repeat is free."""
    key = hashlib.md5(json.dumps([kind, field, asked.strip().casefold(), values], ensure_ascii=False).encode()).hexdigest()
    cached = pipeline.CACHE / "resolved" / f"{key}.json"
    if pipeline.use_cache() and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))["match"], [llm_step("aliases", GEMINI_MODEL, 0, 0, 0, cached=True)]
    api_key = pipeline.model_key()
    if not api_key or len(values) > MAX_VALUES:
        return None, []
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=GEMINI_URL, max_retries=2, timeout=30)
    schema = {"type": "json_schema", "json_schema": {"name": "Match", "schema": {
        "type": "object", "properties": {"match": {"type": "string", "enum": [*values, NONE]}}, "required": ["match"]}}}
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(
        {"asked": asked, "names_a": kind, "by_its": field, "values": values}, ensure_ascii=False)}]
    t0 = time.perf_counter()
    try:
        try:  # picking a name from a list needs no long thinking: low effort answers in about a third of the time
            reply = client.chat.completions.create(model=GEMINI_MODEL, messages=messages, response_format=schema, temperature=0,
                                                   reasoning_effort="low")
        except openai.BadRequestError:  # schema or effort refused: plain JSON mode; the reply is checked below
            reply = client.chat.completions.create(model=GEMINI_MODEL, messages=messages, response_format={"type": "json_object"}, temperature=0)
    except openai.OpenAIError:
        return None, []  # the question goes on as it would have without this step
    usage = reply.usage
    step = llm_step("aliases", GEMINI_MODEL, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0,
                    (time.perf_counter() - t0) * 1000)
    try:
        said = json.loads(reply.choices[0].message.content or "").get("match")
    except (json.JSONDecodeError, AttributeError):
        said = None
    match = said if said in values else None  # an invented value is no value
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"match": match}, ensure_ascii=False), encoding="utf-8")
    return match, [step]


def rename(plan: QueryPlan, scene: Scene) -> tuple[QueryPlan | None, list[TraceStep]]:
    """The plan with every unknown filter value replaced by the drawing's own, and what that cost. No plan when
    nothing is unknown, or when any unknown value has no clear counterpart (then the question goes on as before)."""
    steps, queries, read = [], dict(plan.queries), []
    for name, field, asked, values in unknown(plan, scene):
        match, more = _ask(queries[name].type, field, asked, values)
        steps += more
        if steps:
            steps[-1].detail = f"{field} {asked!r} " + (f"read as {match!r}" if match else "names nothing in this drawing")
        if match is None:
            return None, steps
        queries[name] = queries[name].model_copy(update={field: match})
        read.append(f"{field} {asked!r} read as {match!r}")
    if not read:
        return None, steps
    said = f"{plan.interpretation or ''} The drawing does not use the name in the question: {'; '.join(read)}.".strip()
    return plan.model_copy(update={"queries": queries, "interpretation": said}), steps


def capped(result: Answer) -> Answer:
    """An answer on a renamed plan is no surer than the reading of the name."""
    if result.answer is None:
        return result
    return result.model_copy(update={"confidence": min(result.confidence, CONFIDENCE), "confidence_note":
        f"{result.confidence_note or ''} Capped at {CONFIDENCE:.0%}: a name in the question was matched to the "
        f"drawing's by a model.".strip()})


if __name__ == "__main__":  # no model call: _ask is replaced
    import tempfile
    from pathlib import Path

    from ..executor import execute
    from ..schemas import Entity, Query

    room = lambda i, label, m2, **more: Entity(id=f"room-{i}", type="room", label=label, qty={"m2": m2}, **more)
    scene = Scene(path="data/x.ifc", builder="test@1", tier="parse", covers=["room", "door"], entities=[
        room(1, "Bathroom 1 (A104)", 3.0, level="Level 1", attrs={"long_name": "Bathroom 1"}), room(2, "Küche", 9.0, level="Level 2"),
        Entity(id="door-1", type="door", level="Level 1")])
    area = lambda **f: QueryPlan(queries={"a": Query(op="sum", type="room", qty="m2", **f)}, unit="m2")
    # names the drawing has, exactly or inside a label, are not unknown: no model call
    assert unknown(area(label="küche"), scene) == [] and unknown(area(label="bathroom 1"), scene) == [] and unknown(area(), scene) == []
    assert unknown(area(label="kitchen"), scene) == [("a", "label", "kitchen", ["Bathroom 1 (A104)", "Küche"])]
    assert unknown(area(level="first floor"), scene) == [("a", "level", "first floor", ["Level 1", "Level 2"])]
    assert unknown(QueryPlan(queries={"n": Query(op="count", type="door", label="x")}, unit="count"), scene) == []  # doors carry no labels

    calls = []
    real, pipeline_cache = _ask, pipeline.CACHE

    def fake(kind, field, asked, values):
        calls.append(asked)
        answer = {"kitchen": "Küche", "first floor": "Level 1"}.get(asked)
        return answer, [llm_step("aliases", GEMINI_MODEL, 100, 5, 1.0)]
    _ask = fake
    plan, steps = rename(area(label="kitchen"), scene)
    got = capped(execute(plan, scene))
    assert (got.answer, got.evidence, got.confidence) == (9.0, ["room-2"], CONFIDENCE) and "read as 'Küche'" in got.interpretation
    assert steps[0].detail == "label 'kitchen' read as 'Küche'" and steps[0].usd > 0
    plan, _ = rename(area(label="kitchen", level="first floor"), scene)  # both renamed; nothing is on both, so no answer
    assert (plan.queries["a"].label, plan.queries["a"].level) == ("Küche", "Level 1") and execute(plan, scene).answer is None
    # no counterpart: no plan, the cost is still reported, and nothing is called for a plan with known names
    plan, steps = rename(area(label="sauna"), scene)
    assert plan is None and steps[0].detail == "label 'sauna' names nothing in this drawing"
    n = len(calls)
    assert rename(area(label="Küche"), scene) == (None, []) and len(calls) == n
    assert capped(Answer(error="x")).confidence == 0

    # the real _ask: without a key it does nothing; a stored reading is served from the cache, an invented one is not
    _ask = real
    with tempfile.TemporaryDirectory() as tmp:
        pipeline.CACHE = Path(tmp)
        key, os.environ["GEMINI_API_KEY"] = os.environ.pop("GEMINI_API_KEY", None), ""
        assert _ask("room", "label", "kitchen", ["Küche"]) == (None, [])
        k = hashlib.md5(json.dumps(["room", "label", "kitchen", ["Küche"]], ensure_ascii=False).encode()).hexdigest()
        (Path(tmp) / "resolved").mkdir()
        (Path(tmp) / "resolved" / f"{k}.json").write_text(json.dumps({"match": "Küche"}), encoding="utf-8")
        match, steps = _ask("room", "label", " Kitchen ", ["Küche"])
        assert match == "Küche" and steps[0].cached and steps[0].usd == 0
        if key:
            os.environ["GEMINI_API_KEY"] = key
    pipeline.CACHE = pipeline_cache
    print("ok")
