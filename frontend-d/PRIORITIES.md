# D priorities — 14 November

The judged interface receives hard live questions. This is the active D ordering; the seven-step mock walkthrough is a backup only. Changes remain under frontend-d.

1. **Real-scene integration and one-origin hosting — active.**
   - D entry point serve.py imports the existing FastAPI app and mounts only public browser assets after its API routes.
   - start-api.ps1 starts UI and API together. No backend source is changed.
   - live-smoke.js checks the UI, API, scene, answer, evidence IDs and page images. A stub or abstention exits 2, never a false success.
   - Latest checkout: Swiss, raster detector and room builders remain stubs. A successful real-scene walkthrough is still pending A/B.
2. **Human corrections — local draft tools built; backend integration pending.**
   - Local delete/add-box/type/area/confirm actions and undo are implemented. The reported answer remains unchanged with a pending-correction notice; old/new answer comparison awaits recompute.
   - Use C's returned QueryPlan and POST /api/recompute {scene, plan}; do not implement the executor in JavaScript.
   - C must define correction persistence/JSONL ownership and provenance, plus the supported plan/assumption editing contract. D supplies edits and UI; no fake recalculation or fabricated training records.
3. **Upload flow.**
   - Current D local PDF/image preview is not upload analysis.
   - Integrate C's upload identifier, accepted formats, reader/build progress and error contract; then add IFC/DXF, multi-page source selection and two-point known-length scale calibration.
   - Scans/hand drawings require an explicit unverified state. Reader type, scale and confidence must come from agreed data.
4. **Complex answers.**
   - Formula-variable colours require explicit variable-to-entity bindings.
   - Show real Nemotron tool-call trace, reported cost/confidence and same-drawing answer history.
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
- Upload request/response, reader stage reporting, supported file limits and persistent drawing identity.
- Formula bindings, floor alignment/scale metadata and tool-call trace shape where existing schema cannot express them.

Do not change shared schemas or invent these fields inside D.
