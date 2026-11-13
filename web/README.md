# Lane D: viewer and dashboard

You show the drawing, the answer and its evidence, and the cost and confidence numbers. You can build against the examples in [`fixtures/`](../fixtures/) today; nothing here waits for the other lanes.

## What the API gives you

Run it from the repo root with `uv run --extra brain uvicorn blueprints.api:app --reload`, and point your dev server's proxy at it for `/api`.

| Call | Returns |
|---|---|
| `POST /api/ask` with `{"path": "...", "question": "..."}` | an `Answer` |
| `GET /api/scene?path=...` | the `Scene` for a drawing; 501 until that drawing's builder exists |
| `GET /api/image?path=...` | the image file named by `Page.image` |

`path` is always a dataset path such as `data/swiss_dwellings/site1101_bldg2241_floor10275.pdf`.

## Scene: what to draw

Example: [`fixtures/scene.example.json`](../fixtures/scene.example.json). It is a hand-made mock of a real Swiss plan; its page image is that drawing's PNG in the dataset.

| Field | Use |
|---|---|
| `pages[i].image`, `width`, `height` | Load the image from `/api/image?path=<image>`. Width and height are its pixels. |
| `entities[]` | Everything found in the drawing. |
| `entities[].geom` | A polygon as `[x, y]` pairs, each 0..1 of the page image, origin top-left. Multiply by the displayed size. A box is 4 corners, so one `<polygon>` draws everything. |
| `entities[].page` | Which page the polygon is on. |
| `entities[].type`, `subtype`, `label`, `number` | What to call it in a tooltip or list. |
| `entities[].qty` | Quantities by unit, such as `{"m2": 4.49}`. |
| `entities[].conf` | 0..1. Draw low values differently, for example amber and dashed, so the checker looks there first. |
| `entities[].level`, `apartment` | For grouping, and for stacking floors in the 3D view. |
| `covers` | The kinds of things this scene was searched for. |

## Answer: what to show

Example: [`fixtures/answer.example.json`](../fixtures/answer.example.json).

| Field | Use |
|---|---|
| `answer`, `unit` | The headline. `answer` is `null` when there is none; show `error`. |
| `confidence` | 0..1, for the gauge. |
| `tier` | Badge: `parse`, `detect`, `llm` or `none`. |
| `evidence` | Entity ids to highlight. Look them up in the scene's `entities`. |
| `extra_entities` | More things to highlight that are not in the scene. Same shape as scene entities. |
| `formula` | The computation as text, such as `5.08 + 4.06 = 9.14 m2`. |
| `interpretation`, `assumptions` | How the question was read, and the values behind a derived answer. Good candidates for editable fields. |
| `trace`, `cost_usd` | Steps with model, tokens, GPU seconds, cost and time, and the total. |

## Run logs: what the dashboard reads

`eval.py` writes one JSON object per line to `runs/*.jsonl`. Example: [`fixtures/run.example.jsonl`](../fixtures/run.example.jsonl). The shape is `EvalRow` in [`blueprints/schemas.py`](../blueprints/schemas.py).

| Field | Use |
|---|---|
| `source`, `type` | Group by these. |
| `correct` | `true`, `false`, or `null` for free-form text that is not auto-scored. |
| `confidence` | Bin it against `correct` for the reliability diagram. |
| `tier`, `cost_usd`, `ms` | Cost and tier breakdowns; the cost-against-accuracy chart. |
| `path`, `question`, `gold`, `pred`, `error` | The failure table; link each row into the viewer. |

`runs/` is git-ignored. For a first number to plot, run `uv run python eval.py --baseline`.

## If the shapes do not fit

The schemas are shared. If the viewer needs a field that is not there, ask for it in the team chat; do not work around it in the frontend.
