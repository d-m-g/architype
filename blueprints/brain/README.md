# Lane C: question understanding, escalation, cost, API

You turn a question into a `QueryPlan`, handle what the cheap path cannot, and own the price tables and the HTTP API.

| File | What it does |
|---|---|
| `plan.py` | `make_plan(question, scene)` and `plan_with_trace`: Gemini turns any question, any language, into a `QueryPlan`; cached in `plans.json` and `.cache/plans/`. |
| `vision.py` | `read_scene(path)`: Gemini reads a floor plan once and returns a `Scene` (rooms with label, number, apartment, printed area; floors; apartments; doors; windows; fixtures; each with a box). Refuses drawings that are not floor plans. |
| `escalate.py` | `escalate(...)`: drawings with no scene are answered on the vision scene; anything still unanswered or low-confidence goes to the Nemotron agent with scene tools. |
| `baseline.py`, `baselines.html` | Plain-model baselines and their report. |
| `HANDDRAWN.md` | The hand-drawn plan: annotation set and self-checks. |
| `../costs.py` | `TOKEN_PRICES`, `GPU_PRICES`. Every lane builds its `TraceStep`s with `llm_step` and `gpu_step`. |
| `../api.py` | The HTTP API and the sign-in. |

## What lane C provides to the other lanes

**HTTP API** (`blueprints/api.py`; the demo serves it with the UI on one origin):

| Endpoint | Use |
|---|---|
| `POST /api/ask {path, question}` | The answer. `path` is a dataset path or an `uploads/...` id. |
| `POST /api/upload?filename=x.pdf` | Store a drawing (raw body; pdf, png, jpg; 50 MB max). Returns `{path, reader}`. |
| `GET /api/scene?path=` | The scene: from a builder for dataset drawings, from the vision reader for uploads. |
| `GET /api/image?path=` | The page image (`data/...`, `uploads/...` or a rendered `pages/...`). |
| `GET /api/plan?question=` | The plan alone, for re-running on an edited scene. |
| `POST /api/recompute {scene, plan}` | Run a plan on a corrected scene; no model call. |
| `POST /api/login {password}`, `POST /api/logout` | Only when `BLUEPRINTS_PASSWORD_HASH` is set; then every other route needs the session cookie. |

**Reading an `Answer` for the card (lane D):**
- `evidence` are scene ids; for drawings without a builder scene (uploads, vision fallback) the same entities also arrive in full in `extra_entities`, with `geom`, so the viewer never needs a second lookup.
- `trace` lists every step with its model, tokens, cost and time. Step names: `plan` (Gemini; `cached: true` when free), `scene` (the vision read, only on the first question of a drawing), `execute`, `escalate` (one per Nemotron turn), `tool:list_entities`, `tool:run_plan`, `tool:look` (the agent's tool calls, in order), `look` (the Gemini call behind a `tool:look`). Show the `tool:` steps as Nemotron's tool trace.
- `interpretation` carries the agent's one-sentence explanation; `error` says why when `answer` is null (including "not a floor plan").
- `tier="llm"` marks vision and agent answers; confidence is fixed at 0.7 (vision) and 0.6 (agent) until calibration.

**For lanes A and B:** call `vision.read_scene(path)` from a builder if you want its rooms or floors, and add its `scene.trace` to your scene's trace so the cost is counted. Never import `openai` yourselves.

**Settings** (environment): `GEMINI_API_KEY`, `VERDA_INFERENCE_KEY` (Nemotron); `BLUEPRINTS_VISION_MODEL` (default `gemini-3.8-flash`; `gemini-4-carbon` reads better at ~3x the cost); `NEMOTRON_URL`, `NEMOTRON_MODEL`, `NEMOTRON_KEY_VAR` to switch Nemotron provider; `BLUEPRINTS_PASSWORD_HASH` from `python -m blueprints.api hash`.

**Measured** on eval.py's 300 test questions: plain Gemini 4 Carbon 78.3%, plain Flash 72.7%, vision path 68% (Flash reader) / 71% (Carbon reader) with evidence on every answer. Nemotron answers multi-step questions (largest room, rooms over 12 m²) in 3–6 tool turns for about $0.002, and declines when the drawing lacks the data.

All models are in [`../schemas.py`](../schemas.py).

## QueryPlan: what you produce

A plan is one or more named queries, and a formula when there is more than one.

```json
{"queries": {"area": {"op": "sum", "type": "room", "apartment": "A", "outdoor": false, "qty": "m2"}},
 "unit": "m2"}
```

| Field | Meaning |
|---|---|
| `queries` | Name to `Query`. The names are the variables of `formula`. |
| `formula` | Arithmetic over query names and numeric assumptions. The executor evaluates it in code; allowed are `+ - * / //`, parentheses and `ceil floor round min max abs`. Leave it out when there is one query. |
| `unit` | Unit of the final answer: `count`, `m2`, `m`, `m3`, `ft2`. |
| `interpretation` | The reading you chose for an ambiguous question, such as toilet bowls against toilet rooms. Shown to the user. |
| `assumptions` | Values the user may want to change: tile size, waste. Numeric ones can be used in `formula`. |

Each `Query`:

| Field | Meaning |
|---|---|
| `op` | `count` the selected entities, or `sum` one quantity over them. |
| `type`, `subtype` | Which entities. `subtype` must be a word from `SUBTYPES`. |
| `label`, `number`, `level`, `apartment` | Exact text filters, ignoring case. "room 'ESIK (57-2)' on '4.korrus'" is `label` ESIK, `number` 57-2, `level` 4.korrus. |
| `outdoor` | `false` for the area questions that exclude balconies, loggias and terraces. |
| `where` | Equality on `Entity.attrs`, for schedule columns and the like. |
| `qty` | For `sum`: which quantity, `m2`, `m`, `m3` or `ft2`. |

A derived question is the same thing with a formula; see [`fixtures/plan.tiles.example.json`](../../fixtures/plan.tiles.example.json):

```json
{"queries": {"area": {"op": "sum", "type": "room", "label": "Bagno", "qty": "m2"}},
 "formula": "ceil(area * (1 + waste) / tile_m2)", "unit": "count",
 "assumptions": {"tile": "40 x 40 cm", "tile_m2": 0.16, "waste": 0.1}}
```

When you ask Gemini for a plan, pass `QueryPlan.model_json_schema()` as the structured-output schema and validate the reply with `QueryPlan.model_validate_json`. A reply that fails validation is a failed parse, not a plan.

## Answer: what the pipeline returns

Example: [`fixtures/answer.example.json`](../../fixtures/answer.example.json).

| Field | Meaning |
|---|---|
| `answer`, `unit` | The result. `answer` is `null` when the question could not be answered; `error` says why. |
| `confidence` | Probability the answer is correct, 0..1. This is what gets calibrated. |
| `tier` | The most expensive tier that contributed: `parse`, `detect`, `llm`, or `none`. |
| `evidence` | Ids of scene entities to highlight. |
| `extra_entities` | Evidence the escalation model found that the scene does not have, as full entities with `geom`. |
| `formula` | The computation in readable form. |
| `trace`, `cost_usd` | The steps that produced this answer and their summed cost. The scene's build cost is in `Scene.trace`. |

## escalate

It is called when the cheap path gave no answer or one below `pipeline.ESCALATE_BELOW`. `scene` is `None` for drawings with no builder, such as the scans: those are read by `vision.read_scene` and answered with the normal executor. If that gives no answer, or a builder's answer is low-confidence, the Nemotron agent reasons over the scene with `list_entities`, `run_plan` (the exact executor; it never does arithmetic itself) and `look` (Gemini on a crop), and finishes with `answer`. Questions no plan can express (the largest or smallest room, comparisons, area thresholds, "which") are marked unsupported by the planner instead of being forced into a sum; the verdict is cached, and those questions always go to the agent. It returns a new `Answer` with `tier="llm"` and keeps `draft.trace`. If Nemotron does not answer within 40 s, the same loop runs on Gemini Flash (the trace starts with an `escalate:unavailable nemotron-...` step and the `escalate` steps name the model that answered), and Nemotron is skipped for 5 minutes. The whole agent has a 2-minute budget; raising `NotImplementedError` (no model answered, no answer in 8 turns) keeps the draft.

## Cost

`costs.llm_step` raises `KeyError` for a model with no price. That is deliberate: a missing price must not be logged as free. Price Nemotron at a paid rate.

## Check your work

```bash
uv sync --extra brain
uv run --extra brain uvicorn blueprints.api:app --reload
uv run python eval.py --limit 500
uv run python test_contract.py
```

Every plan comes from Gemini (`gemini-3.8-flash`), whatever the language or wording. Quoted names are masked first, so one call serves every question of the same shape. `plans.json` (committed) holds Gemini's plans for the dataset's 1,261 question shapes, so eval runs without a key; new shapes are cached in `.cache/plans/`. Check and re-seed with `uv run --env-file .env --extra brain python -m blueprints.brain.plan [--seed]`: it grades the cached plans against hand-written readings of the English templates and requires every non-English question to plan like its English twin. Keys go in environment variables; `.env` is git-ignored.
