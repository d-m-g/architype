"""Lane C. Question -> QueryPlan.

Every plan comes from Gemini Flash, whatever the language or wording. Quoted names are masked first
('Bagno', «Balkon», „ESIK (57-2)“ -> {label}), so one call serves every question of the same shape. Plans are cached:
- plans.json, committed: Gemini's plans for the dataset's question shapes (`--seed`), so eval runs without a key;
- .cache/plans/, local: shapes first seen at run time.
No regex or hand-written table answers anything; the hand-written English RULES below only grade the model.

Check:  uv run python -m blueprints.brain.plan          (seed: add --seed, needs GEMINI_API_KEY)
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from .. import pipeline
from ..costs import llm_step
from ..schemas import SUBTYPES, Query, QueryPlan, Scene, TraceStep

# Quoted names in every style the dataset uses: «Balkon», « Séjour », „ESIK (57-2)“, "x", 'Bagno'.
# A straight quote opens only after a non-letter, so d'étage and dell'appartamento are not quotes.
_QUOTED = re.compile(r"«\s*([^»]*?)\s*»|„([^“”]*)[“”]|“([^”]*)”|\"([^\"]*)\"|(?<!\w)'(.+?)'(?!\w)")


def mask(question: str) -> tuple[str, dict[str, str]]:
    """'Raum 7.01 («Balkon»)' -> ('Raum 7.01 ({label})', {'label': 'Balkon'})."""
    slots: dict[str, str] = {}

    def keep(m: re.Match) -> str:
        name = "label" if not slots else f"label{len(slots) + 1}"
        slots[name] = next(g for g in m.groups() if g is not None)
        return "{" + name + "}"

    return _QUOTED.sub(keep, question.strip()), slots


def _fill(plan: dict, slots: dict[str, str]) -> QueryPlan:
    text = json.dumps(plan, ensure_ascii=False)
    for name, value in slots.items():
        text = text.replace("{" + name + "}", json.dumps(value, ensure_ascii=False)[1:-1])
    return QueryPlan.model_validate_json(text)


SEEDED_FILE = Path(__file__).parent / "plans.json"
SEEDED: dict[str, dict] = json.loads(SEEDED_FILE.read_text(encoding="utf-8")) if SEEDED_FILE.exists() else {}

GEMINI_MODEL = "gemini-3.8-flash"  # Flash-Lite misread room numbers on 1-3 of 72 shapes per run; Flash 0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_PROMPT = f"""You turn a question about one construction drawing (any language) into a QueryPlan, as JSON matching this schema:
{json.dumps(QueryPlan.model_json_schema())}

How the plan is executed: each query selects entities of one type from the drawing, then counts them (op "count")
or sums one quantity over them (op "sum" with qty). Filters are exact text matches; leave out any you do not need.
- Entity types: room, apartment, level (a floor or storey), door, window, fixture (sanitary items), wall, slab, column, row (a schedule row), other.
- Subtypes: {json.dumps({k: sorted(v) for k, v in SUBTYPES.items()})}. Leave subtype out if none fits.
  A toilet bowl, WC pan or washbasin is a fixture; a room that holds them is a room (wc_room, bathroom).
- label, number, level, apartment filter on the names written in the drawing. Only names the question quotes or
  identifies (room 7.01, apartment B) are filters; abbreviations or examples in parentheses just explain the
  question. When a room is given by its number and its name, as in room 7.01 ({{label}}), filter on number "7.01"
  and leave the name out. Apartments are identified by the apartment filter.
- outdoor: false when the question excludes balconies, loggias, terraces or other outdoor spaces; otherwise leave it out.
- formula: arithmetic over query names and numeric assumptions, with + - * / // ( ) ceil floor round min max abs.
  Required when there are several queries ("how many A and B" is two counts added). Never compute numbers yourself.
- assumptions: values the user may want to change (tile size in m2, waste). interpretation: the reading you chose, if ambiguous.
- unit of the final answer: count, m2, m, m3 or ft2.
- Quoted names in the question are replaced by placeholders such as {{label}}. Copy a placeholder verbatim into
  the filter it belongs to.
Examples:
"Quelle est la surface de la pièce 2.14 ({{label}}) ?" ->
{{"queries": {{"area": {{"op": "sum", "type": "room", "number": "2.14", "qty": "m2"}}}}, "unit": "m2"}}
"How many 40x40 cm tiles do I need for the floors of the rooms labelled {{label}}?" ->
{{"queries": {{"area": {{"op": "sum", "type": "room", "label": "{{label}}", "qty": "m2"}}}},
 "formula": "ceil(area * (1 + waste) / tile_m2)", "unit": "count",
 "interpretation": "Floor tiles only.", "assumptions": {{"tile": "40 x 40 cm", "tile_m2": 0.16, "waste": 0.1}}}}
Reply with the JSON only."""


def _ask_gemini(template: str) -> tuple[QueryPlan, list[TraceStep]]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise NotImplementedError("lane C: this question's plan is not cached, and GEMINI_API_KEY is not set")
    import openai
    from pydantic import ValidationError

    client = openai.OpenAI(api_key=key, base_url=GEMINI_URL, max_retries=4, timeout=60)
    messages = [{"role": "system", "content": _PROMPT}, {"role": "user", "content": template}]
    schema = {"type": "json_schema", "json_schema": {"name": "QueryPlan", "schema": QueryPlan.model_json_schema()}}
    steps: list[TraceStep] = []
    for _ in range(2):  # one retry, with the validation error
        t0 = time.perf_counter()
        try:
            try:
                reply = client.chat.completions.create(model=GEMINI_MODEL, messages=messages, response_format=schema, temperature=0)
            except openai.BadRequestError:  # schema refused: JSON mode; the prompt carries the schema
                schema = {"type": "json_object"}
                reply = client.chat.completions.create(model=GEMINI_MODEL, messages=messages, response_format=schema, temperature=0)
        except openai.OpenAIError as e:
            raise NotImplementedError(f"lane C: Gemini call failed: {type(e).__name__}: {e}") from None
        usage = reply.usage
        steps.append(llm_step("plan", GEMINI_MODEL, usage.prompt_tokens if usage else 0,
                              usage.completion_tokens if usage else 0, (time.perf_counter() - t0) * 1000))
        text = reply.choices[0].message.content or ""
        try:
            return QueryPlan.model_validate_json(text), steps
        except ValidationError as e:
            error = e
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That is not a valid QueryPlan:\n{e}\nReply with the corrected JSON only."}]
    raise NotImplementedError(f"lane C: Gemini's plan failed validation twice: {error}")


def plan_with_trace(question: str, scene: Scene | None) -> tuple[QueryPlan, list[TraceStep]]:
    """The plan and what it cost: a $0 cached step on a cache hit, Gemini's steps otherwise."""
    template, slots = mask(question)
    local = pipeline.CACHE / "plans" / (hashlib.md5(template.encode()).hexdigest() + ".json")
    plan = SEEDED.get(template) or (json.loads(local.read_text(encoding="utf-8")) if local.exists() else None)
    if plan is not None:
        return _fill(plan, slots), [llm_step("plan", GEMINI_MODEL, 0, 0, 0, cached=True)]
    new, steps = _ask_gemini(template)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(new.model_dump_json(exclude_defaults=True), encoding="utf-8")
    return _fill(new.model_dump(exclude_defaults=True), slots), steps


def make_plan(question: str, scene: Scene) -> QueryPlan:
    return plan_with_trace(question, scene)[0]


# ---- grading only: hand-written readings of the dataset's English templates. Never used to answer. ----

def _count(type: str, subtype: str | None = None, **filters) -> QueryPlan:
    return QueryPlan(queries={"n": Query(op="count", type=type, subtype=subtype, **filters)}, unit="count")


def _area(**filters) -> QueryPlan:
    return QueryPlan(queries={"area": Query(op="sum", type="room", qty="m2", **filters)}, unit="m2")


RULES = [
    (r"^How many windows are (shown|drawn) on this floor plan", lambda m: _count("window")),
    (r"^How many doors\b.*this floor plan", lambda m: _count("door")),
    (r"^How many floors \(storeys\)", lambda m: _count("level")),
    (r"^How many (toilet bowls|toilets \(WC\))", lambda m: _count("fixture", "toilet")),
    (r"^How many (sinks and wash basins|washbasins)", lambda m: _count("fixture", "sink")),
    (r"^How many showers and bathtubs", lambda m: QueryPlan(
        queries={"showers": Query(op="count", type="fixture", subtype="shower"),
                 "bathtubs": Query(op="count", type="fixture", subtype="bathtub")},
        formula="showers + bathtubs", unit="count")),
    (r"^How many bathtubs", lambda m: _count("fixture", "bathtub")),
    (r"^How many showers are", lambda m: _count("fixture", "shower")),
    (r"^How many saunas", lambda m: _count("room", "sauna")),
    (r"^How many kitchens", lambda m: _count("room", "kitchen")),
    (r"^How many bedrooms", lambda m: _count("room", "bedroom")),
    (r"^How many bathrooms or washrooms", lambda m: _count("room", "bathroom")),
    (r"^How many separate WC rooms", lambda m: _count("room", "wc_room")),
    (r"^How many balconies", lambda m: _count("room", "balcony")),
    (r"^How many apartments", lambda m: _count("apartment")),
    (r"^How many rooms labelled '(.+)' are there on this floor", lambda m: _count("room", label=m[1])),
    (r"^What is the total net area in m² of all rooms labelled '(.+)' on this floor", lambda m: _area(label=m[1])),
    (r"^What is the net floor area of room (\S+) \('", lambda m: _area(number=m[1])),
    (r"^What is the net floor area of apartment (\S+) in m²", lambda m: _area(apartment=m[1], outdoor=False)),
    (r"^What is the total net floor area in m² of all numbered indoor rooms on this floor", lambda m: _area(outdoor=False)),
]


def _rule(question: str) -> QueryPlan | None:
    return next((build(m) for pattern, build in RULES if (m := re.match(pattern, question))), None)


def _canon(plan: QueryPlan) -> str:
    """Equal for plans that compute the same thing, whatever the query names or term order."""
    from ..executor import evaluate
    queries = {n: q.model_dump_json(exclude_defaults=True) for n, q in plan.queries.items()}
    names = {n: int(hashlib.md5(s.encode()).hexdigest()[:8], 16) % 9973 + 1 for n, s in queries.items()}
    names |= {k: v for k, v in plan.assumptions.items() if type(v) in (int, float)}
    try:
        value = evaluate(plan.formula, names) if plan.formula else next(iter(names.values()))
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
        value = f"bad formula: {e}"
    return json.dumps([plan.unit, sorted(queries.values()), value], ensure_ascii=False)


SEED_SOURCES = ("cubicasa5k", "swiss_dwellings", "ifc_models")  # ifc_bench and schedules are for the escalation agent


def _seed(rows: list[dict]) -> None:
    """Ask Gemini once per dataset question shape that plans.json lacks, and add the answers to plans.json.
    Each answer lands in .cache/plans/ as it arrives (plan_with_trace caches it), so a rerun resumes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    todo = sorted({mask(r["question"])[0] for r in rows if pipeline.source_of(r["path"]) in SEED_SOURCES} - SEEDED.keys())
    print(f"seeding {len(todo)} question shapes with {GEMINI_MODEL}", flush=True)

    def one(template):  # a template has no quotes left, so plan_with_trace caches it under its own text
        try:
            return template, plan_with_trace(template, None), None
        except NotImplementedError as e:
            return template, None, str(e)

    usd, failed = 0.0, []
    with ThreadPoolExecutor(8) as pool:
        for i, done in enumerate(as_completed([pool.submit(one, t) for t in todo]), 1):
            template, result, error = done.result()
            if result is None:
                failed.append((template, error))
            else:
                SEEDED[template] = result[0].model_dump(mode="json", exclude_defaults=True)
                usd += sum(s.usd for s in result[1])
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}  ${usd:.4f}  {len(failed)} failed", flush=True)
    lines = [f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(SEEDED.items())]
    SEEDED_FILE.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    print(f"seeded {len(todo) - len(failed)} for ${usd:.4f}; {len(failed)} failed")
    for template, error in failed[:10]:
        print("  FAILED", template, "|", error[:200])


def _self_check() -> None:
    from collections import Counter, defaultdict

    assert mask("Combien de pièces portant la désignation « Séjour » y a-t-il à cet étage ?")[1] == {"label": "Séjour"}
    assert mask("Quelle est la surface nette de l'appartement B en m² ?")[1] == {}
    assert mask("Wie gross ist die Nettofläche von Raum 7.01 («Balkon») in m²?") == ("Wie gross ist die Nettofläche von Raum 7.01 ({label}) in m²?", {"label": "Balkon"})
    assert mask("How many rooms labelled „ESIK (57-2)“ on 'level 4'?")[1] == {"label": "ESIK (57-2)", "label2": "level 4"}
    assert _canon(_rule("How many showers and bathtubs are drawn on this floor plan?")) == _canon(QueryPlan.model_validate(
        {"queries": {"b": {"op": "count", "type": "fixture", "subtype": "bathtub"}, "s": {"op": "count", "type": "fixture", "subtype": "shower"}},
         "formula": "b + s", "unit": "count"}))

    file = pipeline.DATA_ROOT / "questions.jsonl"
    if not file.exists():
        print(f"skipped: {file} not found")
        return
    rows = [json.loads(line) for line in file.open(encoding="utf-8")]
    if "--seed" in sys.argv:
        _seed(rows)

    total, seeded = Counter(), Counter()
    graded, wrong = Counter(), Counter()
    plans = defaultdict(lambda: (Counter(), Counter()))  # path -> (canon of English plans, canon of the others)
    for r in rows:
        source, question = pipeline.source_of(r["path"]), r["question"]
        total[source] += 1
        template, slots = mask(question)
        if template not in SEEDED:
            continue
        seeded[source] += 1
        got = _canon(_fill(SEEDED[template], slots))
        expected = _rule(question)
        if expected:
            graded[source] += 1
            if got != _canon(expected):
                wrong[source, template, json.dumps(SEEDED[template], ensure_ascii=False)] += 1
        if source in ("cubicasa5k", "swiss_dwellings"):
            plans[r["path"]][expected is None][got] += 1

    print(f"{'source':18s} {'questions':>9s} {'planned offline':>15s}")
    for source in sorted(total):
        print(f"{source:18s} {total[source]:9d} {100 * seeded[source] / total[source]:14.1f}%")
    n = sum(total.values())
    print(f"{'ALL':18s} {n:9d} {100 * sum(seeded.values()) / n:14.1f}%")

    n_wrong = sum(wrong.values())
    print(f"\nEnglish questions where Gemini's plan differs from the hand-written reading: {n_wrong} of {sum(graded.values())}")
    for (source, template, plan), count in wrong.most_common(15):
        print(f"  {count:5d} {source}: {template}\n        gemini: {plan}")
    differ = [p for p, (en, other) in plans.items() if en != other]
    print(f"drawings where the non-English plans differ from their English twins: {len(differ)} of {len(plans)}")

    unplanned = [s for s in ("cubicasa5k", "swiss_dwellings") if seeded[s] != total[s]]
    assert not unplanned, f"plans.json does not cover every question of {unplanned}; run with --seed"
    assert not n_wrong and not differ, "Gemini's plans disagree with the hand-written readings or across languages"
    print("ok: every cubicasa5k and swiss_dwellings question has a cached Gemini plan that matches the hand-written "
          "English reading and its English twin")


if __name__ == "__main__":
    _self_check()
