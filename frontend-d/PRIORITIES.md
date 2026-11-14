# D priorities — 14 November

The judged interface receives hard live questions. This is the active D ordering; the ten-step mock walkthrough is a backup only. Changes remain under frontend-d.

1. **Real-scene integration and one-origin hosting — active.**
   - Production blueprints.api and the local D entry point share the public-asset allowlist. D checks the actual /openapi.json contract, so no frontend-only health route is required.
   - start-api.ps1 starts UI and API together. No backend source is changed.
   - live-smoke.js checks the UI, API, scene, answer, evidence IDs and page images. A stub or abstention exits 2, never a false success.
   - Pulled the lane A Swiss parser and lane C Nemotron/vision workflow. A real Swiss question now passes through the production API into D: 26 unique windows, all evidence IDs resolved, page image loaded, and the duplicate-count explanation displayed. Provider-backed vision/agent calls still require server keys.
2. **Human corrections — local draft tools built; backend integration pending.**
   - Local delete/add-box/type/area/confirm actions and undo are implemented. The reported answer remains unchanged with a pending-correction notice; old/new answer comparison awaits recompute.
   - POST /api/recompute {scene, plan} now exists. Integration remains gated on the exact QueryPlan associated with the answer; do not implement the executor in JavaScript.
   - C must define correction persistence/JSONL ownership and provenance, plus the supported plan/assumption editing contract. D supplies edits and UI; no fake recalculation or fabricated training records.
3. **Upload flow.**
   - PDF/PNG/JPEG preview and explicit raw-byte upload are connected; returned uploads/ IDs work with live questions. WebP remains local-only.
   - Reader name and upload/scene/question stages are shown. Backend stage events, IFC/DXF, multi-page ingestion (currently first PDF page only) and two-point known-length scale calibration remain pending.
   - Scans/hand drawings require an explicit unverified state. Reader type, scale and confidence must come from agreed data.
4. **Complex answers.**
   - Formula-variable colours require explicit variable-to-entity bindings.
   - Session-local per-drawing answer history is implemented, with original evidence snapshots and explicit question reuse. Real Nemotron tool-call detail still depends on the returned trace contract.
   - Never imply tool calls from a model-name badge alone.
5. **Floor plates, if time permits.**
   - Multi-page shared scenes now display source-image plates with linked evidence; single-page scenes retain illustrative room extrusions.
   - Explode slider, one-page isolation and 2D selection sync are built against a synthetic two-page fixture. Agreed alignment transforms and optional real IFC elevations remain.
   - Hide this production stack for single-page drawings. Preserve the existing clearly labelled illustrative mock preview until the stack is implemented.
6. **Throughout.**
   - Deliberate loading, error and abstention states; cost/confidence on live answers.
   - Rehearse a real live path; keep mock backup and record a backup video after that path is stable.

Cut floor stacking first, then the optional Gemini-audio voice upgrade.

## Backend handoff needed

- Real Swiss Scene, Answer and image paths are integrated. Next verify the same flow with model-backed uploads and Nemotron on the configured demo server.
- Answer.query_plan (exact agreed field/type), recompute response/error shape and correction logging policy.
- Reader stage reporting, multi-page ingestion and additional file formats; basic upload contract is integrated.
- Formula bindings, floor alignment/scale metadata and tool-call trace shape where existing schema cannot express them.

Do not change shared schemas or invent these fields inside D.

## Current integration boundary

Answer.extra_entities are retained and listed even when /api/scene fails. Without page metadata their normalized geometry is kept as reported but is not projected onto an invented image. Missing referenced IDs remain explicit. Tool steps, interpretations, formulas and reported costs use the backend Answer. No new schema fields or query execution are implemented in D.
