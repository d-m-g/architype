# Lane C: question understanding, escalation, cost, API

You turn a question into a `QueryPlan`, handle what the cheap path cannot, and own the price tables and the HTTP API.

| File | What it does |
|---|---|
| `plan.py` | `make_plan(question, scene)` and `plan_with_trace`: Gemini turns any question, any language, into a `QueryPlan`; cached in `plans.json` and `.cache/plans/`. |
| `vision.py` | `read_scene(path)`: Gemini reads a floor plan once and returns a `Scene` (rooms with label, number, apartment, printed area; floors; apartments; doors; windows; fixtures; each with a box). Refuses drawings that are not floor plans. |
| `room_types.py`, `room_types.json` | `add_room_types(scene)`, called by `pipeline.get_scene` for every scene: room labels in any language get a room type (Küche → kitchen, Bagno → bathroom), unlabelled rooms get one from the fixtures inside them (bathtub/shower → bathroom, toilet only → wc_room). The scene claims `room:<type>` coverage only when every room is typed. 71 dataset labels are seeded (reviewed); new labels cost one Flash call per drawing, cached. |
| `escalate.py` | `escalate(...)`: drawings with no scene are answered on the vision scene; anything still unanswered or low-confidence goes to the Nemotron agent with scene tools. |
| `baseline.py`, `baselines.html` | Plain-model baselines and their report. |
| `HANDDRAWN.md` | The hand-drawn plan: annotation set and self-checks. |
| `../costs.py` | `TOKEN_PRICES`, `GPU_PRICES`. Every lane builds its `TraceStep`s with `llm_step` and `gpu_step`. |
| `../api.py` | The HTTP API and the sign-in. |

## What lane C provides to the other lanes

### Google voice: push-to-talk and spoken answers

With `GEMINI_API_KEY` configured on the server, the site's existing Dictate question
streams microphone audio to Google's Live transcription model. Draft words appear
in the transcript preview as the user speaks; finalized text goes into the composer
after recording finishes. Pressing Ask finishes recording, waits for the finalized
transcript, and submits the question. A timed Starting/Connecting indicator stays
visible until the audio stream is ready; Ask is disabled during setup. Each recording is limited to 30
seconds. Read aloud sends
the exact displayed answer summary to `/api/voice/speak`, which returns WAV audio.
The drawing detector remains responsible for counting. No Google key is sent to
the browser; the same login middleware protects voice requests. The backend issues
a single-use, two-minute ephemeral token locked to the transcription model and
language configuration. The browser sends 100 ms chunks of 16 kHz mono PCM over
Google's constrained Live WebSocket endpoint. No WebSocket forwarding is needed
on the app host. This server does not save recordings. Interactions requests
disable storage (`store: false`). Google voice requires HTTPS or localhost access.

Defaults: `gemini-3.5-transcribe-live`, `gemini-3.5-transcribe` for recorded fallback,
and `gemini-3.8-flash-tts`, with voice `Kore`.
Override via `BLUEPRINTS_TRANSCRIBE_MODEL`, `BLUEPRINTS_TTS_MODEL`, and
`BLUEPRINTS_TTS_VOICE`. The key's project must have access/quota for these models.
`BLUEPRINTS_LIVE_TRANSCRIBE_MODEL` selects the live model; setting
`BLUEPRINTS_LIVE_TRANSCRIPTION=0` keeps recorded dictation instead. If the live
connection fails, the UI offers recorded dictation on the next attempt. Browsers
without Web Audio worklets use recorded dictation. No new SDK dependency is
required. Restart the API after setting its key.

The picker offers English, Finnish, Swedish, German, French, Italian and Spanish,
plus automatic input-language detection. Answer-summary templates follow the
selected language; raw answer text/provider errors are not translated. Auto uses
the English summary template. If server voice is unconfigured or the browser lacks
recording support, existing browser dictation/read-aloud remain available where
supported. Continuous Gemini Live conversations are not part of this feature.

API: `GET /api/voice/config`, `POST /api/voice/live-token {language}` (never cache),
`POST /api/voice/transcribe?language=fi-FI` (raw audio,
8 MiB limit), `POST /api/voice/speak {text}` (4,000-character limit). Input formats
include WebM/Opus, OGG and M4A. Provider errors are surfaced with API keys redacted.
Cancellation discards late replies and releases microphone tracks. Development
proxy routes preserve audio bodies and content types. A cancelled or unfinalized
live transcript never overwrites the question. AudioWorklet and live-connection
modules are in the public asset allowlist.

Offline checks: `python -m unittest blueprints.brain.test_voice` (needs httpx as a
test dependency), and `node --test frontend-d/voice.test.js frontend-d/voice-live.test.js`. Provider calls are
mocked; actual transcription/speech quality must be checked with event credentials.


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
- Numeric agent answers must include an executable final `plan`. The server derives `answer`, `unit`, `formula`, `assumptions` and `evidence` from that same execution, and generates `interpretation` from the source entities rather than copying model-written arithmetic. Invalid or missing plans are retried; an unverified final answer is withheld.
- `interpretation` carries the source caption for verified numeric agent answers, or the agent's explanation for text answers; `error` says why when `answer` is null (including "not a floor plan"). Offline regression checks: `uv run --extra brain python -m unittest blueprints.brain.test_escalate`.
- `tier="llm"` marks vision and agent answers. Verified numeric agent answers keep the executor's source-derived score without a fixed cap, with arithmetic verification explained separately; this is not an overall probability of correctness. Vision answers still use the fixed 0.7 heuristic and free-form text agent answers use 0.6 until calibration.

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

## resolve: look at the data first

`pipeline.answer` no longer plans blind. The order for a question is:

1. **A cached plan** (`plan.cached_plan`): the question's shape is in `plans.json` or was planned before. Answered by the executor exactly as before, at $0. Every scored dataset question takes this path.
2. **Otherwise `resolve.resolve`**, which depends on no wording having been seen before:
   - `inventory(scene)`: what the scene holds, from the data: types and counts, which sizes are recorded and what they mean, labels, numbers, levels and apartments as written, room links, `covers`, and what is NOT recorded. About 570 tokens for a Swiss plan, capped at 6,000 characters.
   - One call to `GEMINI_MODEL` with the question and the inventory. It returns a `Decision`: `direct` (a recorded value, or a selection or aggregation), `computed` (arithmetic over recorded values; assumptions only for what a drawing cannot hold), or `missing` (names the missing data; `needs` lists what a person could supply). It writes SQL, never a number.
   - `execute`: the SQL runs on an in-memory SQLite copy of the scene (`entities`, and `links(entity_id, room_id)` for which room a door, window or fixture belongs to). One SELECT only, an authorizer that allows reading those two tables and a list of functions and nothing else, a row cap, a step and time limit. A query must return the ids its result rests on (`id`, or `ids = group_concat(id)`): no ids, no answer. `covers` is honoured as in the executor; unresolved or ambiguous room links refuse a per-room result. A query error goes back to the model once.
   - Answers that are not one number are composed in code: `answer: "top"` names the first-ranked row and reports ties (`"Zimmer B1 and Zimmer B2 (tied)"`), `"rows"` lists names.
   - The decision is cached under `.cache/decisions/` by (question, inventory), so a repeat on the same drawing is free and nothing is shared between drawings whose data differs.
3. **Other sources only after that**, and only where one exists. On a parsed file (`tier="parse"`) the resolver's result is final: an abstention says what is missing and offers `needs` as assumptions (`"needed: the ceiling height in metres"`), which the user can state in the question or send as `assumptions` to `/api/ask`. On an image scene, unresolved room links first get the bounded vision refinement (`room_link_vision`), and an abstention goes to the agent below, which may look at the picture.

**Follow-up questions.** The server stays stateless: the browser sends the conversation's earlier turns in `history` on `/api/ask` (and on `/api/recompute` for a corrected scene). A turn is small: question, answer and unit or the abstention reason, interpretation, the query that produced it, assumptions, up to 12 evidence ids with their count, and the drawing's path. History is untrusted: `resolve.clean_history` keeps only those fields, cut to size, the last 4 turns within 3,000 characters, drops evidence ids that are not in the current scene, and marks a turn about another drawing. It reaches the same one decision call as quoted JSON lines above the question, with rules added to the system prompt: use it only to resolve what the question refers to, write a self-contained query, and return the standalone reading as `interpretation`. History is context, not data: a number that is an earlier answer's value is refused in a formula or assumption unless the user states it. The decision cache key includes the history sent. A question with a cached plan takes the free path with or without history. The agent gets the same capped history.

Check: `uv run python -m blueprints.brain.test_resolve` (canned model replies, no key). The free-form question set and its runner are in [`benchmarks/freeform/`](../../benchmarks/freeform/).

## escalate

The agent has the same data tools: its context is the scene's inventory, `query` runs one read-only SELECT (in place of the count-and-sum-only `run_plan`), and a final number, ranking or list must come with a `decision` that the server executes.

It is called when the cheap path gave no answer or one below `pipeline.ESCALATE_BELOW`. `scene` is `None` for drawings with no builder, such as the scans: those are read by `vision.read_scene` and answered with the normal executor. If that gives no answer, or a builder's answer is low-confidence, the Nemotron agent reasons over the scene with `list_entities`, `query` (read-only SQL run by the server; it never does arithmetic itself) and `look` (Gemini on a crop), and finishes with `answer`. It returns a new `Answer` with `tier="llm"` and keeps `draft.trace`. If Nemotron does not answer within 40 s, the same loop runs on Gemini Flash (the trace starts with an `escalate:unavailable nemotron-...` step and the `escalate` steps name the model that answered), and Nemotron is skipped for 5 minutes. The whole agent has a 2-minute budget; raising `NotImplementedError` when no model answered keeps the draft. Reaching 8 turns without a verified final answer returns an explicit abstention instead of publishing an unverified number.

On a scene with `tier="parse"` (DXF, vector PDF, IFC) the agent is not given `look`, and `_tool` refuses it if called. Such a file holds exact geometry, so no model resolves an area or a dimension: sizes are the quantities the parser recorded (`list_entities` returns each entity's `qty` and `sizes`; the convention is in [`../parsers/README.md`](../parsers/README.md), "Sizes"), and a size that is not recorded is answered "cannot answer", naming the missing measurement. `look` stays for `tier="detect"` scenes, where the picture is all there is.

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
