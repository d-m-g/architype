# D frontend: evidence viewer and evaluation dashboard

## The current page (14 November, evening)

`index.html` now loads a rewritten shell. Everything below this section describes the page before it, whose files are still here but no longer loaded.

| File | Does |
|---|---|
| `index.html`, `app.css` | Markup and the one stylesheet. Dark by default; the moon button switches. |
| `main.js` | Drawing viewer, the ask flow and its progress states, answer card, evidence list, trace, question history, tabs and 3D toggle. |
| `start.js` | Start screen: drop or choose a file (it uploads and opens in one step, with upload progress), dataset samples, open by path. |
| `edit.js` | Correct mode: remove, confirm or draw a detection. The answer is recalculated through `/api/plan` and `/api/recompute`, with no model call. |
| `evaluation.js` | Evaluation tab: load one or more `runs/*.jsonl` logs and compare them. |

Still loaded unchanged: `api-client.js`, `contracts.js`, `local-file.js`, `editor-bridge.js`, `history-state.js`, `voice.js`, `room-renderer.js`, `geometry-3d.js`, `floor-stack.js`, `floor-stack-state.js`, and `zoomAt` from `mock-scene.js`.

No longer loaded: `app.js`, `intake.js`, `workspace.js`, `corrections.js`, `correction-state.js`, `calibration.js`, `calibration-state.js`, `history.js`, `demo.js`, `dashboard.js`, `shared-dashboard.js`, `mock-evaluation.js`, `dashboard.html`, `plan.svg`, `styles.css`, `workspace.css`, `dashboard.css`. Their tests still run. To go back to the previous page, restore `index.html` from git; nothing else needs to change.

### General view and dictation

After a question, General view fits the full source page and shows every detected layer without clearing the answer or history. The control switches to Answer view to return to cited highlights. It is disabled during correction mode. Fit by itself changes only the camera.

The microphone shows Ready, Listening, Transcribing and Review question states. Interim speech appears in a separate Live transcript panel. Only a completed transcript replaces the question draft, and the user still chooses Ask. Cancellation, speech errors, manual typing or switching drawings discard pending recognition without overwriting the typed draft. Browser speech APIs and playback remain unchanged.

## The previous page

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
- Answer: `extra_entities` merge by ID; identical repeated scene entities are accepted, while conflicting duplicates are rejected; unknown evidence IDs are reported rather than silently verified. Units, reported confidence, tier, interpretation, read-only assumptions, formula, trace/model/tokens/GPU/time/cache/cost are displayed. Query and scene-build costs remain separate.
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

The dataset builders remain stubs. The pulled vision reader can analyse uploads when its provider is configured; local verification currently returns the explicit missing GEMINI_API_KEY response, so no real inference success is claimed. No API keys, dataset files, generated caches, or Python environments are committed. Reference answers are never sent to `/api/ask`.

Proxy tests cover upstream response codes/body, byte-preserving images, source path forwarding, unavailable API, private runtime-file exclusion and local-only destination enforcement. Client requests the scene before the answer so a successful scene build can populate the team's cache. Missing-scene responses still allow the API's structured abstention to be shown. Requests have timeouts; a return to mocks aborts pending work and ignores stale replies.


## Browser voice assistance

The Editor now has language-selected dictation (English, Finnish, German, French, Italian) and optional English answer read-aloud. Dictation replaces the question only after a final transcript; review it before submitting. It never calls the team API automatically. Concurrent typing is preserved, cancelled transcripts are ignored, and leaving the editor stops speech. Unsupported browsers retain normal typed input.

Recognition requires browser support and microphone permission, and may use the browser vendor's remote speech service. No audio is stored or sent to our backend. Read-aloud identifies mock data, reported confidence and uncertain evidence; null answers remain abstentions. Reading stops when the answer changes. Actual microphone/service accuracy must be checked on the demo machine.

Validation: 21 Node tests, including cancelled recognition, concurrent edits, zero answers and spoken abstention.

## Guided mock demo

Use **Start mock demo** above the workspace for ten manually controlled steps covering evidence, quantities, uncertainty, corrections with undo, local scale calibration, abstention, 3D, voice, answer history and dashboard. The guide makes no API calls. Starting replaces the active viewer example; exiting leaves the current demo state. See DEMO.md for presenter cues, honest limitations and the live-data handoff.

## Live judging alignment (14 November update)

The editor prioritizes **Add a drawing**. PDF, PNG, JPEG, WebP, GIF, BMP and AVIF files up to 25 MB can be selected for a browser-local preview, with content-signature checks, an editable question draft, replacement and close. Bytes stay local until the explicit upload action. The prior viewer/answer is hidden during preview and restored on close; the question draft is retained. PDF page 1 is rendered locally without the browser PDF plugin; its original bytes remain the upload source.

**Upload is connected to the team API:** the explicit upload action sends raw PDF/PNG/JPEG bytes to POST `/api/upload?filename=...`, preserves the returned `uploads/` identifier, and shows the reported reader. The UI input limit is 25 MiB (backend upload limit: 50 MiB). WebP, GIF, BMP and AVIF are decoded in the browser and converted to PNG before upload; animated images use their first frame. PNG/JPEG/PDF bytes are retained. Rasters above 32 megapixels or 16,384 pixels per side are scaled down in the browser to fit before preview and upload (the server reader works at 2,048 px on the long side anyway); `/api/image` serves the same scaled-down size for oversized rasters already on the server. Asking a question then loads the scene and answer through the existing endpoints. Missing provider configuration is shown as an error/abstention, never mock evidence. The current reader uses the first PDF page; IFC/DXF, multi-page ingestion, stage events and scale calibration remain pending. A/C own ingestion and readers.

For existing server drawings, expand **Use a drawing already on the team server**, enter its data/ or uploads/ path and select **Use live drawing**. The question button switches to **Ask live drawing** and sends arbitrary text verbatim, including Finnish and multi-step questions. It does not imply that the current backend can answer them. Progress distinguishes scene loading and question answering. Cancel rejects late responses; retry restores the last submitted path/question. Missing source scenes never inherit mock evidence. Typed and dictated text use the same API.

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

The team now exposes POST /api/recompute and GET /api/plan, but Answer still does not include the exact plan used for that answer. Recalculation remains intentionally disabled until that association and integration are available; generating a new plan would not guarantee the same calculation. No executor runs in JavaScript, no model calls are made, and edits are not uploaded or appended to training JSONL. Logging and editable plan assumptions require the agreed backend contracts.

Validation: 30 Node checks. Browser verified deletion/undo, type changes, area changes, amber confirmation, numeric and pointer-drawn boxes, panel navigation, 3D isolation and complete undo to the original scene.

## Question and answer history

Expand **Questions & answer history** below the answer trace. The drawing selector groups mock and real paths separately. Each drawing retains its latest 20 results in this browser tab, newest first; reloading clears history. Entries include the submitted question, timestamp, answer/status, available reported confidence/cost, and immutable original scene/answer snapshots. Abstentions, errors and explicit cancellations are included; loading, pan/zoom, selection and correction actions do not create entries.

**Open saved answer** restores the saved result and evidence without rerunning inference. Source images may still be fetched from their original paths; history does not archive image bytes. **Reuse question** restores the drawing and fills its question, then waits for an explicit submit. It does not send conversation history or reference answers to the API. Imported Answer JSON has no question field, so its question is labelled unavailable and reuse is disabled.

Correction drafts stay separate and are retained for the matching scene revision. Opening saved history exits draft-overlay mode so the saved evidence is visible. Pending requests and asynchronous imports cannot replace a restored history entry.

Validation: 34 Node checks, including immutable snapshots, drawing isolation, retention, zero/null answers and read-only history navigation. Browser checked mock evidence restoration, explicit resubmission, nonduplicating history navigation, and separate live-error history.


## Pulled API compatibility check

Aligned D with team commit bdc8893: raw uploads, uploads/ source paths, proxy upload/plan/recompute routes and identical repeated vision evidence. Browser verification confirmed a real PNG upload, the reported reader, and deliberate missing-provider abstention. Automated checks cover byte preservation, route forwarding, evidence deduplication and existing viewer/history/correction behavior.

## Local scale calibration

Expand Scale calibration in the editor. Select two source points, or enter page percentages with the keyboard, then supply a positive known length in m/cm/mm/ft and save. The marked reference is a local uniform-scale draft; it never changes the scene, answers, areas, correction drafts or 3D and makes no API calls. Drafts are isolated by drawing path, page image and dimensions, retained while switching views, and cleared on reload. Undo reverses saves and resets; Escape cancels point selection. Calibration of perspective-distorted scans is not supported.

## Upload and live recovery

Upload progress offers Cancel upload while keeping the selected preview and editable question. Failed or cancelled uploads can be retried; a successful upload carries the latest question draft into the editor. Cancelling locally does not guarantee the server stopped processing or storing a request. Live progress distinguishes scene loading from answering. Missing provider setup, unavailable readers, timeouts, connection failures and file errors have recovery guidance. Retry current question retains the selected drawing and submits the current text, including edits made after a failure. Structured Answer.error responses also offer retry; ordinary evidence abstention remains distinct. Mock samples and Start mock demo remain explicit opt-in fallbacks. No API or schema changes.

## Keyboard and narrow screens

Skip to editor moves focus to the editor heading, including from other panels. Focus rings and scroll offsets keep controls visible below sticky navigation. Dashboard case tables can be focused and scrolled with the keyboard. At narrow widths, toolbars and long source/answer text wrap; calibration/correction coordinate forms use one column on small phones, and inputs retain readable sizing. Existing SVG arrow-key pan, zoom buttons, numeric calibration/box alternatives and evidence lists remain available. View and connection status changes use polite announcements. Screen-reader speech output still needs manual assistive-technology validation.

## Multi-page floor plates

Shared Scene inputs with two or more pages show page images as visual plates in 3D. The explode slider changes display spacing; One page isolates the active page. Plate buttons, object outlines and the page selector synchronize with the existing 2D page and evidence selection. Single-page drawings keep the existing illustrative room preview. Try the local `fixtures/floor-stack.scene.json` and `fixtures/floor-stack.answer.json` pair through Sources → Shared schema input. This fixture repeats one available source image on two pages to test interaction; it is not a real multi-floor drawing. Plate position, order and spacing are visual only: there is no inferred alignment, elevation or measured 3D geometry. A/C must supply actual page images and alignment transforms for a real stack.


## Production workflow integration — 14 November

Connection checks read GET /openapi.json and verify the actual ask, scene and image methods. The local proxy forwards that contract too. This works with the Docker entry point blueprints.api:app without relying on /__frontend/api-status. Expired sessions show the backend error and ask the user to sign in again while keeping the working tab open.

The current Swiss reader has been exercised through the real API, drawing image and browser overlays. The live-smoke.js check validates the current UI, API methods, answer evidence IDs and page images without loading evaluation answers or seeding caches. The question is sent only as {path, question}.

Vision/agent answers can carry full extra_entities even when no separate Scene is available. D retains these source references, quantities and evidence IDs, but does not invent page dimensions or overlay coordinates. The answer remains marked source unavailable until the backend provides a matching page. Trace tool steps, interpretation and formula are shown as reported. Recompute still requires the exact plan associated with the original answer; GET /api/plan alone does not establish that association.


## Appearance

Use the Dark mode button in the header or Settings → Appearance to choose Light, Dark or Follow device setting. The choice is stored on this browser origin, applied before the initial paint, and synchronized across open tabs. Follow device responds to system appearance changes. If browser storage is blocked, the current tab still switches and explains that the choice is temporary.

Navigation, evidence cards, forms, evaluation tables, notices and 3D surroundings share the theme. Source drawing images and their evidence colour conventions are preserved; no inversion filter is applied. Switching appearance keeps the current question, answer and evidence in place. The dashboard redirect returns to the same themed workspace.

Swiss verification results for all 65 floor plans and 2,358 question records in the supplied preview are recorded in SWISS-VERIFICATION.md, including the 150 duplicate-policy reference disagreements. No parser code was modified.

After pulling the updated agent budget, live question requests and the local proxy allow up to five minutes for scene extraction plus agent work. The UI explains the wait and keeps Cancel available; uploads and connection checks retain their shorter limits.


## Uploaded source preview

After upload, the main viewer immediately loads the returned source through /api/image, independently of /api/scene and /api/ask. Source dimensions come from decoding the actual uploaded image, not invented scene data. The source stays visible while a question runs, after cancellation, and when model setup or analysis is unavailable. A received Scene replaces the preview with its own page image and verified coordinate mapping. Preview-only state supplies no extracted entities or fabricated overlays and does not enable geometry-dependent corrections or 3D.

PDFs use a rendered first-page image in the same source viewer until the reader provides its own rendered page. The preview reports the actual PDF page count and clearly limits analysis to page 1. Uploading from Sources or 3D returns to the 2D editor. Late image loads cannot replace a newer selected source; history reopens the matching source path without a model call.

Browser verification covered PNG, JPEG, WebP, animated GIF, BMP and AVIF uploads, PNG normalization, immediate display, missing-model-key recovery with the source retained, PDF previews, and switching back to a parsed Swiss Scene with all 26 window overlays. The animated GIF fixture's converted PNG was checked against its first frame. TIFF, HEIC, SVG and CAD uploads require export to a supported format; browser decoding failures are reported before upload.

### Drawing navigation and detected regions

Scroll over the drawing to zoom around the pointer; drag to pan, pinch on touch screens, or double-click to zoom in (Shift-double-click zooms out). Fit drawing restores the whole page. Camera updates are painted once per frame and shared editor updates are deferred during continuous movement.

API scene regions on the current page are clickable even when not cited by the answer. Dashed blue outlines identify these other detections; numbered highlights remain answer evidence. Click or use Enter/Space on a region to inspect its label, type, available quantity and source reference. Clicking a region preserves the camera; selecting an evidence row centres that evidence. No new geometry or quantities are inferred. Correction and calibration tools retain their own gestures.

Viewer status and navigation instructions are below the source in Drawing information & controls. The source canvas uses the available viewport size and centres the complete page with its original proportions; empty margins use the viewer background instead of extending the white drawing sheet. Source-embedded annotations are preserved.

### Uploaded PDF previews

Uploaded PDFs render page 1 as an image in the regular SVG drawing viewer instead of embedding the browser PDF plugin. The original PDF is uploaded unchanged. A server Scene replaces this preview with the server's own page raster before drawing normalized API geometry, so highlights use exactly the same coordinates as the detections. The current uploaded-PDF reader analyses page 1 only; the preview states the real PDF page count without suggesting other pages were analysed.

PDF.js 5.4.624 is loaded lazily from jsDelivr (library, worker, fonts and decoder assets). PDF bytes are rendered locally and are not sent to the CDN. This network dependency is necessary within the frontend-only deployment scope because the backend's public asset allowlist does not yet include PDF.js vendor files. If it is unavailable, local preview explains the failure and still permits uploading the original PDF for server analysis. A future backend allowlist update can enable self-hosting these pinned assets. Raster uploads and dataset readers retain their existing paths.

Clicking a detected region opens and scrolls to its source evidence in the answer panel. Cited regions expand their matching evidence row with the source reference, page and extraction method; other detections show a separate source card explicitly labelled as not cited. Neither action changes the reported answer or its evidence count. The selection stays synchronized with the drawing and 3D.

### Shared scene loading and honest overviews

Overview and question requests share one extraction per drawing in this tab. Up to eight successful scenes are retained; failures can be retried. Cancelling stops a caller from accepting a late response, while the shared extraction may continue so a retry can join it. Extraction has the same five-minute budget as live questions. Reload the page to refresh a cached scene after a reader update.

Follow-up questions keep the existing source, camera and highlights visible, with a Previous result label while answering. Failed transport requests and cancellation retain that result rather than recording it as the new question's answer. A successful answer on the same source page preserves the camera.

Overview counts and areas are scoped to the active page. Missing room coverage is shown as unavailable; subtype-only coverage is labelled partial. Room counts include detections without drawable geometry. Indoor area summaries exclude outdoor rooms, identify missing quantities, preserve reported zeros and are labelled partial when coverage or quantities are incomplete. They are reported room-area sums, not verified building footprint measurements.

Source JSON imports, dataset-path entry and API diagnostics are under Settings > Advanced settings (collapsed by default). Ask and Evaluate remain in the primary navigation. Existing #sources links still open the advanced settings section and preserve the current drawing and answer.

### Add drawing flow

Add drawing opens one card with Choose file, Review and Open drawing steps. File selection, preview, original filename, size/conversion details, optional question and upload/retry actions stay together. Open drawing explicitly uploads the file and starts its scene overview; the drafted question is not submitted. Users can replace a file, cancel preparation, cancel upload or return to the current drawing. Closing the card discards the pending preview and restores the current drawing. Server-path entry lives under Settings > Advanced settings. Original filenames are retained as viewer/history labels while API requests keep using content-addressed source paths.
