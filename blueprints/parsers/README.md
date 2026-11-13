# Lane A: parsers and executor

You turn structured drawings into a `Scene` and own the code that answers a `QueryPlan` from one.

| File | What to do |
|---|---|
| `swiss.py` | `build_scene(path, file)` for Swiss plans. Start here: 30% of all questions. |
| `ifc.py` | `build_scene(path, file)` for IFC models. Last: 3% of all questions. |
| `../executor.py` | Works today for count, sum and formulas. Extend it when a question type needs more. |
| `../detector/rooms.py` | After Swiss: rooms by type and floors on rasters, 32% of all questions. |

## The Scene you return

Defined in [`../schemas.py`](../schemas.py); a full example is [`fixtures/scene.example.json`](../../fixtures/scene.example.json).

| Field | What to put there |
|---|---|
| `path` | The `path` argument, unchanged. |
| `builder` | Your code's name and a version, such as `swiss_dxf@1`. |
| `tier` | `"parse"`. |
| `covers` | Every kind of thing you looked for, as `type` or `type:subtype`. See below. |
| `pages` | One `Page` per image the viewer shows, with its pixel size. |
| `entities` | One `Entity` per room, door, window, fixture, apartment and so on. |
| `trace` | Leave empty unless building the scene cost money. |

Each `Entity`:

| Field | What to put there |
|---|---|
| `id` | Unique in the scene and the same on every rebuild, such as `room-5.06`. Answers cite it as evidence. |
| `type`, `subtype` | `type` is one of the `EntityType` words. `subtype` must be in `SUBTYPES`: `toilet`, `sink`, `bathtub`, `shower` for fixtures. |
| `label`, `number` | Text as drawn, split in two: `"Bagno"` and `"5.06"`. Questions filter on each exactly, ignoring case. |
| `level`, `apartment` | Labels as drawn: `"4.korrus"`, `"A"`. |
| `outdoor` | `true` for balconies, loggias and terraces. The area questions exclude them. |
| `geom` | Polygon in page coordinates, each value 0..1 of the page image, origin top-left. Pixels are rejected. |
| `qty` | Quantities keyed by unit: `{"m2": 4.49}`. Units are `m2`, `m`, `m3`, `ft2`. |
| `attrs` | Anything else a question may filter on, such as schedule columns. |
| `conf` | Leave at 1.0 for parsed entities. |
| `ref` | Where it came from: `dxf:<handle>`, `ifc:<GlobalId>`. |

### covers

A count of zero only means "none" if you looked. List a key in `covers` when your parser finds every entity of that kind, including when this drawing happens to have none. The executor refuses to answer about a key that is not covered, so the question escalates and never comes back as a confident zero.

A query with a subtype needs `type:subtype` covered; a query without one needs `type`.

## What the Swiss questions need

| Question | Entities |
|---|---|
| apartments | `apartment`, one per unit outline |
| doors, windows | `door`, `window` |
| toilets, washbasins, bathtubs, showers | `fixture` with the subtype |
| rooms labelled 'X', area of room N, area of rooms labelled 'X' | `room` with `label`, `number`, `qty.m2` |
| area of apartment A, total indoor area | `room` with `apartment` and `outdoor` set |

Things worth knowing:

- Questions name the `.pdf`. The same-name `.dxf` has one layer per kind of thing, and the same-name `.png` is the page image.
- DOOR, WINDOW and FIXTURE layers are missing from a few files. That means zero of them, and the key is still covered.
- Gold areas were computed from geometry, so they can differ from the printed labels by 0.01. That is inside the 2% tolerance.

## Check your work

```bash
uv sync --extra parsers
uv run python eval.py --source swiss_dwellings --limit 200 --fresh
uv run python test_contract.py
```

`--fresh` rebuilds scenes; without it you read the cache in `.cache/scenes/swiss_dwellings/`. Delete that folder when your builder changes.

The question rules in `brain/plan.py` already cover every English Swiss question, so your accuracy shows up as soon as the scenes are right. Questions in other languages stay unanswered until lane C's parser exists.
