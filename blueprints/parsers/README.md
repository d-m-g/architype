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
| `qty` | Quantities keyed by unit: `{"m2": 4.49}`. Units are `m2`, `m`, `m3`, `ft2`, `wall_m2`. One value per unit: see Sizes. For a room, call `outlines.measure(room, outline, ...)`: it adds `wall_m2` (wall area net of the doors and windows on the outline, at the room's height or an assumed 2.5 m, noted in `attrs.wall_height_assumed`) and `m` (perimeter) when the room has none yet. |
| `attrs` | Anything else a question may filter on, such as schedule columns, and the sizes that do not fit `qty`. |
| `conf` | Leave at 1.0 for parsed entities. |
| `ref` | Where it came from: `dxf:<handle>`, `ifc:<GlobalId>`. |

### covers

A count of zero only means "none" if you looked. List a key in `covers` when your parser finds every entity of that kind, including when this drawing happens to have none. The executor refuses to answer about a key that is not covered, so the question escalates and never comes back as a confident zero.

A query with a subtype needs `type:subtype` covered; a query without one needs `type`.

### Sizes

A parser records every dimension it knows. For a format with hard measurements (DXF, vector PDF, IFC) no model ever resolves an area or a dimension: the parser does, and a model only chooses which entities and which recorded quantity to use. A size the parser did not record is not answered; the escalation agent has no picture-reading tool on a parsed scene.

A query sums one `qty` unit, and an entity holds one value per unit, so each unit means one thing per entity type:

| Type | `qty.m2` | `qty.m` | `qty.m3` | `attrs`, in metres |
|---|---|---|---|---|
| `room` | net floor area | perimeter of its outer outline | | `width_m`, `length_m`, `height_m` |
| `door`, `window` | | width: the opening's length along the wall | | `height_m`, `thickness_m` (the wall's, at the opening) |
| `wall` | net side area: one face, less openings | length | net volume | `height_m`, `thickness_m` |
| `slab` | top area | | net volume | `thickness_m` |

- Everything in `attrs` is a number in metres with the unit in its name. Do not repeat a `qty` value there.
- A room's `width_m` and `length_m` are its extent across and along its own longest wall, not the page axes, so a room drawn at an angle measures the same. `length_m` is the larger. For an L-shaped room they are the box around it, so `width_m * length_m` is more than its area.
- A door's width is its opening in the wall. Never take it from the bounding box of its footprint, which may hold the swing arc, nor from which side of its frame is longer: `swiss_openings.size` reads it from where the frame meets the wall ends and the room outlines.
- Leave a size out when it cannot be determined. No width is better than a guessed one: the question is then refused instead of answered wrongly.
- Say how a size was obtained when it was not read from the file as stated. The IFC parser sets `attrs.size_source` to `quantity set` or `attributes` (the model states it), `geometry` (measured on the element's body), or `quantity set; geometry: height_m, thickness_m` when only the named ones were measured; rooms keep `attrs.area_source` for the area. DXF and PDF sizes are all measured on the drawn geometry, so those parsers set no source.

What each parser records:

| Parser | Sizes | From |
|---|---|---|
| `swiss.py` (`swiss_dxf@7`, `swiss_dxf_upload@2`) | room area, perimeter, width, length; door and window width and wall thickness | The DXF geometry, which is in metres. |
| `pdf.py` (`pdf_vector@2`) | the same | The PDF paths times the scale (the title block's `1:100`, else calibrated on printed room areas). With no scale nothing is in metres, so nothing is recorded. |
| `ifc.py` (`ifc@3`) | room area, perimeter, width, length, height; wall length, net side area, volume, height, thickness; door and window width and height; slab top area, volume, thickness | Quantity sets and `OverallWidth` / `OverallHeight` first, converted to metres; else the element's body. Volumes are only ever the model's own. Measured on the body, a wall's length and thickness come from its footprint and its side area is volume over thickness less its openings; a slab's top area is its plan area over the cosine of its slope. A slab's stated thickness (`Depth` or `Width`, which exporters use differently) and net area are checked against the body before use. |

When a builder's entities change, bump its version string. `pipeline.get_scene` rebuilds a cached parsed scene whose builder is not the current one, so nobody has to delete `.cache/scenes/`.

## What the Swiss questions need

| Question | Entities |
|---|---|
| apartments | `apartment`, one per unit outline |
| doors, windows | `door`, `window` |
| toilets, washbasins, bathtubs, showers | `fixture` with the subtype |
| rooms labelled 'X', area of room N, area of rooms labelled 'X' | `room` with `label`, `number`, `qty.m2` |
| area of apartment A, total indoor area | `room` with `apartment` and `outdoor` set |
| perimeter, wall area, paint (not in the dataset) | `room` with `qty.m` and `qty.wall_m2` |

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

`--fresh` rebuilds scenes; without it you read the cache in `.cache/scenes/swiss_dwellings/`. Bump your builder's version string when its entities change, and the cached scenes rebuild on their next read.

The question rules in `brain/plan.py` already cover every English Swiss question, so your accuracy shows up as soon as the scenes are right. Questions in other languages stay unanswered until lane C's parser exists.
