# D frontend: evidence viewer and evaluation dashboard

All implementation stays in `frontend-d/`. Shared Python schemas, API, parsers, detectors, evaluation code and fixtures are unchanged.

## Run

From this directory: `python -m http.server 5173 --bind 127.0.0.1`, then open http://127.0.0.1:5173. This serves the mock UI and local JSON/JSONL imports. No dependencies or API keys needed.

For live requests, the frontend host must proxy `/api` to the existing team API (`uv run --extra brain uvicorn blueprints.api:app --reload` from the root). The basic Python static server does NOT provide that proxy. Do not expose the API publicly for this prototype. Missing images/services are reported explicitly; live provider-backed inference has not been verified.

Tests: `node --test viewer.test.js dashboard.test.js contracts.test.js` (11 checks).

## Shared contract integration

`contracts.js` adapts `blueprints/schemas.py` v1 to frontend presentation. It does not modify the team schema or execute queries. Tests read the actual team fixtures. Boundary checks catch version, coordinate, page, duplicate ID, evidence and metric problems; these are frontend checks, not a replacement for backend Pydantic validation.

- Scene: normalized `geom` becomes page pixel coordinates; `page` is zero-based; page selection and evidence selection switch images. Image URLs use `/api/image?path=<encoded Page.image>`. Empty geometry remains listed. `covers` and builder are displayed.
- Answer: `extra_entities` merge only when IDs do not collide; unknown evidence IDs are reported rather than silently verified. Units, reported confidence, tier, interpretation, read-only assumptions, formula, trace/model/tokens/GPU/time/cache/cost are displayed. Query and scene-build costs remain separate.
- API: GET `/api/scene`, POST `/api/ask` with only path/question, GET `/api/image`. Errors and 501 responses do not become answers. Request tokens prevent older responses replacing newer requests or a return to mocks.
- Local imports: open “Shared schema input” and select matching Scene and Answer JSON files. Files stay in browser memory. Images still require the team image endpoint. An Answer has no path field, so callers must ensure it belongs to the selected Scene.
- EvalRow: dashboard imports JSONL with source/type/tier filtering, scored accuracy, answered accuracy, coverage, reported cost/time, confidence bins, and failure links. Null correctness is unscored. Viewer links send only path and question, never gold or predicted answers.
- Imported run logs cannot replay alternative thresholds or compare unseen baselines. Those controls remain available only in the separate synthetic mock mode.

## Preserved mock functionality

Synthetic drawing, all four question examples, numbered overlays, clickable evidence, area formula, amber uncertain candidates, abstention, pan/zoom/fit, keyboard controls, overlay toggle, responsive layout and mock dashboard replay remain available. Sample buttons restore mock mode. `sharedScene` and `sharedAnswers` exports supply equivalent wire-format fixtures; internal presentation labels are not sent across the API.

## Contract limits to discuss with A/C

No changes to shared schemas were made. Schema v1 lacks explicit answer scope, asset attribution/license metadata, an assumptions override request, run/model/split metadata, alternate outcomes for threshold replay, and per-row scene-build/first-versus-repeat costs. These are labelled unavailable, not invented. Derived assumptions stay read-only until C exposes an agreed recalculation interface. Displaying 100% reported confidence does not imply calibration.

## Verification

Eleven tests passed, including team Scene/Answer fixtures, all 12 example EvalRows, coordinate conversion, extra entities, empty geometry, duplicate IDs, missing evidence, null scores, abstentions, empty metrics, and original viewer/dashboard checks. Browser verified shared JSON pair import, six room citations, area formula, missing-image error, return to original mock, and example JSONL import (7/12 correct). Real API/model calls and image rendering through the team server remain unverified. Earlier mock browser tests covered phone/desktop layout, filters, slider, evidence selection, zoom and viewer links.

## Later D work

3D, voice, editable derived assumptions and live run comparisons are not implemented. Backend work remains with A/B/C.

## Unified workspace and 3D seam

`index.html` is the single application. Persistent sidebar panels expose Editor, Dashboard, Sources and Settings; on narrow screens the sidebar becomes a compact top navigation. `dashboard.html` now redirects to `index.html#dashboard` for compatibility. Navigation does not remount panels, so viewer zoom, selection, imports, dashboard filters and replay values remain in memory. Existing scenario links and evaluation links are handled inside the workspace.

The editor's 2D / 3D switch hides/shows separate viewport containers without modifying the 2D camera. 3D is explicitly a preview placeholder, not a working model renderer. It displays current evidence selection. No geometry extrusion, inferred heights, or new backend dependencies were introduced.

Future renderer integration: import `register3DRenderer` from `workspace.js`. Pass an object with `mount(container, {selectEntity})`, `update(snapshot)`, and optional `setVisible(boolean)` / `dispose()`. Manage resize in the renderer. The returned cleanup function detaches it. `editor-bridge.js` publishes scene entities, wire Scene (when available), current Answer, selected entity ID, page index, viewport and mock/shared/pending mode. Source geometry is not silently converted to metres; a renderer must use verified units/transforms. Calling `selectEntity(id)` selects an existing contributing evidence ID in the editor.

Settings provide session-local numbered-label visibility and compact evidence rows. Existing overlays, accessible evidence list, pan/zoom/fit, answers, imports and all dashboard features remain available.

Verification: 13 Node checks, plus browser checks for viewport/selection preservation across sidebar and 2D/3D switches, retained dashboard filters/threshold, sources panel, settings and 375px layout without horizontal page overflow. No browser console errors observed.
