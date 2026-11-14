# D priorities — 14 November

The judged interface receives hard live questions. This is the active D ordering; the seven-step mock walkthrough is a backup only. Changes remain under frontend-d.

1. **Real-scene integration and one-origin hosting — active.**
   - D entry point serve.py imports the existing FastAPI app and mounts only public browser assets after its API routes.
   - start-api.ps1 starts UI and API together. No backend source is changed.
   - live-smoke.js checks the UI, API, scene, answer, evidence IDs and page images. A stub or abstention exits 2, never a false success.
   - Latest checkout: Swiss, raster detector and room builders remain stubs. The team vision reader now supports uploads when configured; local real inference is blocked by missing GEMINI_API_KEY.
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
   - Replace illustrative room extrusions with per-floor source images and linked evidence.
   - Explode slider, isolate floor, agreed alignment transforms; optional real IFC elevations.
   - Hide this production stack for single-page drawings. Preserve the existing clearly labelled illustrative mock preview until the stack is implemented.
6. **Throughout.**
   - Deliberate loading, error and abstention states; cost/confidence on live answers.
   - Rehearse a real live path; keep mock backup and record a backup video after that path is stable.

Cut floor stacking first, then the optional Gemini-audio voice upgrade.

## Backend handoff needed

- Real Scene and Answer for one known drawing; server image paths.
- Answer.query_plan (exact agreed field/type), recompute response/error shape and correction logging policy.
- Reader stage reporting, multi-page ingestion and additional file formats; basic upload contract is integrated.
- Formula bindings, floor alignment/scale metadata and tool-call trace shape where existing schema cannot express them.

Do not change shared schemas or invent these fields inside D.
