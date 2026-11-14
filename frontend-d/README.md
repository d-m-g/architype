# D frontend: evidence viewer and evaluation dashboard

All implementation stays in `frontend-d/`. Shared Python schemas, API, parsers, detectors, evaluation code and fixtures are unchanged.

## Run with the team API

From the repository root, start the frontend in one terminal:

    node frontend-d/dev-server.js

Open http://127.0.0.1:5173. This serves the existing frontend and proxies `/api/ask`, `/api/scene`, `/api/image` to http://127.0.0.1:8000. Both servers bind only to loopback. Set `PORT` or `BLUEPRINTS_API_ORIGIN` before starting if needed; the API origin must remain a loopback HTTP origin.

Start the unchanged team API in a second terminal using the team's documented uv setup, or the D-local environment prepared for this machine:

    ./frontend-d/start-api.ps1 -DatasetRoot 'C:/Users/alexk/OneDrive/Tiedostot/ChatGPT/Junction 2026/dataset-review/junction-dataset-preview'

For another machine, first prepare this isolated environment (or use the team's uv environment):

    python -m venv frontend-d/.runtime
    frontend-d/.runtime/Scripts/python.exe -m pip install fastapi uvicorn

The launcher sets BLUEPRINTS_DATA and an ignored D-local cache; it makes no backend source changes. This minimal environment supports API startup and existing stubs. Additional model/parser dependencies belong to their owners' documented extras.

In Sources, select **Check API connection**, enter a dataset path, then **Ask API with current question**. The question is edited in the Editor. Results and errors are visible in the Editor; mock sample buttons restore mock mode. The proxy health endpoint confirms required API routes, not scene-builder readiness.

The Python static-only server remains usable for mocks but cannot proxy API calls. Never seed real caches with handmade fixtures to make a builder appear implemented.

Tests: run `npm test` from this directory, or run the Node test files listed in package.json directly (19 checks).

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

Editable derived assumptions and live run comparisons are not implemented. Backend work remains with A/B/C.

## Unified workspace and 3D seam

`index.html` is the single application. Persistent sidebar panels expose Editor, Dashboard, Sources and Settings; on narrow screens the sidebar becomes a compact top navigation. `dashboard.html` now redirects to `index.html#dashboard` for compatibility. Navigation does not remount panels, so viewer zoom, selection, imports, dashboard filters and replay values remain in memory. Existing scenario links and evaluation links are handled inside the workspace.

The editor's 2D / 3D switch hides/shows separate viewport containers without modifying the 2D camera. 3D now lazily loads a Three.js renderer. It shows source polygons and illustrative room extrusions for the active page. Heights are display-only, not measured; no model scale or floor alignment is inferred.

Future renderer integration: import `register3DRenderer` from `workspace.js`. Pass an object with `mount(container, {selectEntity})`, `update(snapshot)`, and optional `setVisible(boolean)` / `dispose()`. Manage resize in the renderer. The returned cleanup function detaches it. `editor-bridge.js` publishes scene entities, wire Scene (when available), current Answer, selected entity ID, page index, viewport and mock/shared/pending mode. Source geometry is not silently converted to metres; a renderer must use verified units/transforms. Calling `selectEntity(id)` selects an existing scene entity in the editor without adding it to the answer evidence.

Settings provide session-local numbered-label visibility and compact evidence rows. Existing overlays, accessible evidence list, pan/zoom/fit, answers, imports and all dashboard features remain available.

Verification: 13 Node checks, plus browser checks for viewport/selection preservation across sidebar and 2D/3D switches, retained dashboard filters/threshold, sources panel, settings and 375px layout without horizontal page overflow. No browser console errors observed.


## Interactive illustrative 3D

Three.js 0.185.1 and OrbitControls are vendored locally under `vendor/three`, with MIT license and source/version metadata. No CDN requests or package install are needed at runtime. The renderer loads only when 3D is opened; a failed graphics/module initialization leaves 2D usable.

Orbit by dragging, zoom by scrolling or buttons, reset, or switch to top view. The dropdown provides a keyboard-accessible alternative to picking a mesh. Selection synchronizes with the 2D evidence list when the object contributes to the answer; selecting another scene object does not change the answer. Green shows answer evidence, gold the selection, amber uncertain objects. The existing 2D overlay checkbox applies only to 2D overlays.

Current-page polygons preserve their relative page proportions. Rooms receive a fixed illustrative extrusion; other entities are shallow shapes. No measured heights, real volumes, or cross-page floor stacks are claimed. Empty/nonfinite/zero-area polygons are excluded and pending cases show no stale scene. Rendering is on demand and pauses while hidden. Geometry/materials, controls and resize observers are disposed when replacing the renderer.

Verification: 16 Node tests including page isolation, display proportions and immutable source quantities; browser checks confirmed WebGL rendering, room selection synchronizing to 2D, rotate/zoom/reset/top controls. Full IFC geometry, floor stacking and live backend integration remain later work.


## API integration verification (14 November)

Pulled origin/main through f049a0c without overwriting local D changes. The unchanged team API runs against the existing preview dataset. Verified through the frontend proxy: connection check succeeds; Swiss scene GET returns 501 with lane A's missing-parser message; POST ask returns answer=null / tier=none and that error; PNG image bytes match the dataset file SHA-256. The team contract script passed 10 checks. Browser verified connection status, API abstention/error in the editor, and return to mock area/3D/dashboard functionality.

All scene builders are still stubs in this checkout, so no real question-to-evidence success is claimed. The proxy and UI are connected and ready for their implementations. No API keys, dataset files, generated caches, or Python environments are committed. Reference answers are never sent to `/api/ask`.

Proxy tests cover upstream response codes/body, byte-preserving images, source path forwarding, unavailable API, private runtime-file exclusion and local-only destination enforcement. Client requests the scene before the answer so a successful scene build can populate the team's cache. Missing-scene responses still allow the API's structured abstention to be shown. Requests have timeouts; a return to mocks aborts pending work and ignores stale replies.


## Browser voice assistance

The Editor now has language-selected dictation (English, Finnish, German, French, Italian) and optional English answer read-aloud. Dictation replaces the question only after a final transcript; review it before submitting. It never calls the team API automatically. Concurrent typing is preserved, cancelled transcripts are ignored, and leaving the editor stops speech. Unsupported browsers retain normal typed input.

Recognition requires browser support and microphone permission, and may use the browser vendor's remote speech service. No audio is stored or sent to our backend. Read-aloud identifies mock data, reported confidence and uncertain evidence; null answers remain abstentions. Reading stops when the answer changes. Actual microphone/service accuracy must be checked on the demo machine.

Validation: 21 Node tests, including cancelled recognition, concurrent edits, zero answers and spoken abstention.

## Guided mock demo

Use **Start mock demo** above the workspace for seven manually controlled steps covering evidence, quantities, uncertainty, abstention, 3D, voice and dashboard. The guide makes no API calls. Starting replaces the active viewer example; exiting leaves the current demo state. See DEMO.md for presenter cues, honest limitations and the live-data handoff.

## Live judging alignment (14 November update)

The editor now prioritizes **Bring your drawing**. PDF, PNG, JPEG and WebP files up to 25 MB can be selected for a browser-local preview, with content-signature checks, an editable question draft, replacement and close. No bytes are uploaded. The prior viewer/answer is hidden during preview and restored on close; the question draft is retained. Native PDF preview availability depends on the browser.

**Upload analysis is blocked by the current team API contract:** blueprints/api.py exposes ask, scene and image only, and ask validates dataset paths. The disabled analysis action states this limitation. We have not invented an upload route or changed backend/schema code. To connect it, C must provide the multipart request contract, accepted formats/limits, returned drawing identifier, how that identifier is used for ask/scene/image, processing status and error shape. A/C own file ingestion and drawing readers.

For existing server drawings, expand **Use a drawing already on the team server**, enter its data/ path and select **Use live drawing**. The question button switches to **Ask live drawing** and sends arbitrary text verbatim, including Finnish and multi-step questions. It does not imply that the current backend can answer them. Progress distinguishes scene loading and question answering. Cancel rejects late responses; retry restores the last submitted path/question. Missing source scenes never inherit mock evidence. Typed and dictated text use the same API.

Mocks, local Scene/Answer import, illustrative 3D, dashboard and guided mock demo remain available. Use the mock demo as a labelled fallback, not as a substitute for live judging. The API still has no assumptions-override contract; assumptions remain read-only.

## One-origin FastAPI hosting

Run the team environment from the repository root:

    python -m uvicorn serve:app --app-dir frontend-d --host 127.0.0.1 --port 8000

Or use frontend-d/start-api.ps1 with your dataset root. The D entry point imports the unchanged blueprints.api app and serves the UI at / after registering API routes. Only explicitly listed browser assets are public; Python helpers, tests, environments and caches return 404. The existing Node proxy remains optional for separate frontend development.

For a deployment, point the hosting service's ASGI command at this same entry point and supply the team dependencies/dataset/cache configuration. A public deployment URL has not been provisioned by this change.

Live check (this makes a real ask request; configured providers may incur normal query costs):

    node frontend-d/live-smoke.js http://127.0.0.1:8001 data/swiss_dwellings/site1000_bldg2082_floor9385.pdf "How many windows are on this floor?"

Exit 0 verifies a returned answer, scene, evidence IDs and images; exit 2 means a missing reader/API or abstention. Overlay alignment still requires visual checking on real scenes. See PRIORITIES.md for the correction/recompute, upload and floor-stack sequence and required contracts.

## Local correction drafts

Expand **Correct scene · local draft** below the drawing and enable **Edit draft overlays in 2D**. Select an entity in the dropdown or click its draft outline. Delete false positives, change type, edit an area (m² or ft²), or confirm amber items. Add a missed box using two opposite corners or keyboard-accessible percentage coordinates. Escape cancels box placement.

Every action can be undone in reverse order, including deletion and addition. The change history shows before/after values for type and area. Drafts are isolated by source and entity revision and retained in this browser tab while switching panels, questions on the same scene, and 2D/3D; reload clears them. Different scene revisions never share a draft.

Blue/amber draft overlays replace the original overlays only while correction mode is on. The reported answer, original scene, confidence, evidence list, read-aloud and 3D are unchanged. A visible answer-card notice identifies pending user corrections and says that a new answer is unavailable. Switching to 3D or starting the mock demo restores original overlays and retains the draft.

Recalculation is intentionally disabled until C supplies the returned QueryPlan and the recompute contract. No executor runs in JavaScript, no model calls are made, and edits are not uploaded or appended to training JSONL. Logging and editable plan assumptions require the agreed backend contracts.

Validation: 30 Node checks. Browser verified deletion/undo, type changes, area changes, amber confirmation, numeric and pointer-drawn boxes, panel navigation, 3D isolation and complete undo to the original scene.
