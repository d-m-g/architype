# Blueprints Talk pipeline map

A presentation-ready architecture overview of the current pipeline. Open `pipeline.svg` for a scalable, editable version or use the 3600 × 2460 `pipeline.png` in a deck.

Regenerate both with:

```sh
.venv/bin/python artifacts/pipeline/build_pipeline.py
```

The diagram shows logical data flow; it does not imply the drawing and question builders execute concurrently. Different readers converge on the same `Scene` contract. Vision-only PDF uploads use their dedicated read → plan → execute API path. Uploaded images combine detector symbols with vision room information. The IFC reader is available for dataset drawings; IFC uploads are not currently accepted by the API.

Grounded in these source files:

- `blueprints/pipeline.py`: reader routing, scene caching, planner/executor calls, and escalation when an answer is absent or confidence is below 0.50.
- `blueprints/brain/plan.py`: Gemini query plans, template masking, and seeded/local plan caches.
- `blueprints/brain/vision.py` and `room_types.py`: vision scenes, upload room enrichment, and multilingual room typing.
- `blueprints/brain/escalate.py`: vision fallback, Nemotron tool loop, Gemini provider fallback, and tool traces.
- `blueprints/detector/raster.py` and `train.py`: object detection served on Modal.
- `blueprints/executor.py` and `schemas.py`: deterministic arithmetic, evidence IDs, answer fields, and trace costs.
- `blueprints/api.py`: vision-only PDF routing, recompute endpoint, and explicitly recorded correction labels.

No performance or accuracy numbers are implied. The 0.50 threshold is the current code constant. Confidence on vision and agent answers currently uses fixed values; the map does not claim calibrated probabilities. Corrections can be explicitly recorded for offline training; they do not automatically retrain a detector.
