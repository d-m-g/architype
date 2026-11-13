# Lane C: question understanding, escalation, cost, API

You turn a question into a `QueryPlan`, handle what the cheap path cannot, and own the price tables and the HTTP API.

| File | What to do |
|---|---|
| `plan.py` | `make_plan(question, scene)`. Has rules for the English cubicasa and Swiss templates; add the Gemini parser and a template cache. |
| `escalate.py` | `escalate(question, path, scene, draft)`: the Nemotron agent for low-confidence, multi-step and free-form questions. |
| `../costs.py` | Fill in `TOKEN_PRICES` and `GPU_PRICES`. Every lane builds its `TraceStep`s with `llm_step` and `gpu_step`. |
| `../api.py` | `/api/ask`, `/api/scene`, `/api/image`. Works today; extend it as the viewer needs. |

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

It is called when the cheap path gave no answer or one below `pipeline.ESCALATE_BELOW`. `scene` is `None` for drawings with no builder, such as the scans. Return a new `Answer` with `tier="llm"`, keep `draft.trace` and add your own steps. Raising `NotImplementedError` keeps the draft.

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
