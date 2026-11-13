# Lane B: raster detector

You turn a raster floor plan into a `Scene` of doors, windows and fixtures. Rasters carry 67% of all questions.

| File | What to do |
|---|---|
| `raster.py` | `build_scene(path, file)`: call the detector served on Modal and return a `Scene`. |
| this folder | Your Modal training and serving code. Keep `modal` and model imports inside functions. |
| `rooms.py` | Lane A's: rooms by type and floors. The pipeline adds them to your scene; do not edit it. |

## The Scene you return

Defined in [`../schemas.py`](../schemas.py); an example is [`fixtures/scene.example.json`](../../fixtures/scene.example.json).

| Field | What to put there |
|---|---|
| `path` | The `path` argument, unchanged. |
| `builder` | Model name and version, such as `yolo-cubicasa@3`. |
| `tier` | `"detect"`. |
| `covers` | The classes your model was trained on: `door`, `window`, `fixture:toilet`, `fixture:sink`, `fixture:bathtub`, `fixture:shower`. |
| `pages` | One `Page`: `image` is `path` itself, with the PNG's pixel width and height. |
| `entities` | One `Entity` per detection you would count. |
| `trace` | One `costs.gpu_step("scene", gpu, seconds)` so the cost of building the scene is recorded. |

Each `Entity`:

| Field | What to put there |
|---|---|
| `id` | Stable for the same image and model, such as `det-17`. |
| `type`, `subtype` | `door`, `window`, or `fixture` with `toilet`, `sink`, `bathtub` or `shower`. |
| `geom` | The box as its 4 corners, each value 0..1 of the image. Divide pixels by width and height, and clamp. |
| `conf` | The calibrated probability that this detection is real, not the raw score. |
| `ref` | `det:<index>`. |

`covers` matters: a class you do not list is refused by the executor, and a class you list is trusted to be zero when you return none.

## Counting rules the questions state

- **Doors:** only doorways fitted with a door leaf. Open doorways do not count.
- **Sinks:** kitchen and bathroom together; a double-bowl sink is one.
- **Showers and bathtubs:** emit them as separate subtypes. The plan adds the two counts.

Emit only detections above your threshold: the executor counts every entity in the scene. Its confidence is the product of your `conf` values for now.

## Training and the split

- Train and tune only on drawings where `scoring.split_of(path) == "train"`. The other 20% is the shared test set.
- Dataset names look like the public CubiCasa5K folders: `data/cubicasa5k/high_quality_architectural-1234.png` should be `high_quality_architectural/1234/`. Check a few by eye before relying on it.
- 42% of the rasters are under 800 px on the long side and some are rotated, so upscale small ones and train with rotation.

## Check your work

```bash
uv sync --extra detector
uv run python eval.py --source cubicasa5k --limit 500 --fresh
uv run python test_contract.py
```

The question rules in `brain/plan.py` already cover every English cubicasa question. The baseline to beat on cubicasa is 54.5%, and 14% on windows and 12% on doors.
