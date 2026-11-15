# Make Blueprints Talk

Answers natural-language questions about construction drawings and shows where in the drawing the answer came from. Junction 2026, Stoa challenge. The plan and the reasoning behind it are in [PLAN.md](PLAN.md).

## Quick start

```bash
uv sync                                   # base environment (pydantic only)
uv run python test_contract.py            # the contract check; run it before you push
uv run python eval.py --baseline          # the free baseline on the held-out test split
uv run python eval.py --source swiss_dwellings --limit 200
uv run --env-file .env --extra brain python eval.py --limit 300   # the full pipeline, model steps included
```

Without `--env-file .env` the pipeline has no `GEMINI_API_KEY`, skips every model step and scores lower; `eval.py` warns when that happens.

Put the full dataset at `junction-dataset/` in the repo root (it is git-ignored), or point `BLUEPRINTS_DATA` at it. Each lane installs its own packages with `uv sync --extra parsers`, `--extra detector` or `--extra brain`.

## How a question flows

`blueprints/pipeline.py` is the one path every question takes:

1. `get_scene(path)` builds a `Scene` for the drawing, once, and caches it in `.cache/scenes/`. The builder depends on the source: `parsers/` for Swiss plans and IFC, `detector/` for rasters.
2. `brain/plan.py` turns the question into a `QueryPlan`.
3. `executor.py` runs the plan on the scene and returns an `Answer` with evidence and confidence.
4. If there is no answer or the confidence is low, `brain/escalate.py` gets a turn.

Everything that crosses a lane boundary is a model in [`blueprints/schemas.py`](blueprints/schemas.py). Examples of each are in [`fixtures/`](fixtures/).

## Who owns what

| Lane | Files | Guide |
|---|---|---|
| A: parsers and executor | `blueprints/parsers/`, `blueprints/executor.py`, later `blueprints/detector/rooms.py` | [parsers/README.md](blueprints/parsers/README.md) |
| B: raster detector | `blueprints/detector/` except `rooms.py` | [detector/README.md](blueprints/detector/README.md) |
| C: question understanding, escalation, cost, API | `blueprints/brain/`, `blueprints/costs.py`, `blueprints/api.py` | [brain/README.md](blueprints/brain/README.md) |
| D: viewer and dashboard | `web/` | [web/README.md](web/README.md) |
| Shared | `blueprints/schemas.py`, `scoring.py`, `pipeline.py`, `eval.py`, `test_contract.py`, `fixtures/` | this file |

## Rules that keep merging easy

- **Edit only your lane's files.** If you need something from another lane, ask its owner.
- **Shared files change by agreement,** in a commit that touches nothing else. A schema change also bumps `SCHEMA_VERSION` and updates `fixtures/`.
- **Keep heavy imports inside functions** (`ifcopenshell`, `modal`, `openai`). The pipeline imports every lane, and one missing package must not break the others.
- **Add packages to your own group** in `pyproject.toml`. If `uv.lock` conflicts after a merge, run `uv lock`.
- **Measure with `eval.py` on the test split.** Train and tune only on drawings where `split_of(path) == "train"`.
- **Never look an answer up by filename.**

## Where we start

`eval.py --baseline` always gives the most common training answer for the question's template. It costs nothing, so it is the number to beat:

| Source | Baseline accuracy on the test split |
|---|---|
| All | 41.5% |
| cubicasa5k | 54.5% |
| swiss_dwellings | 14.6% |
| ifc_models | 10.9% |

The pipeline itself scores 0% until the lanes fill in their stubs; `eval.py` lists which stub each unanswered question is waiting for.
