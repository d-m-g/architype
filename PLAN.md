# Make Blueprints Talk: hackathon plan

Junction 2026, Stoa challenge, 4 people, 48 hours. Status: proposal, not yet agreed by the team.

- Challenge: https://hackjunction.app/participate/45/challenges/335
- Dataset preview (0.5 GB): https://fwjydgxpttajiqpfmqtj.supabase.co/storage/v1/object/public/stoa-junction-2026/junction-dataset-preview.zip
- Full dataset (10 GB): https://fwjydgxpttajiqpfmqtj.supabase.co/storage/v1/object/public/stoa-junction-2026/junction-dataset.zip
- Judging, 25% each: accuracy, how well the reasoning is visualised so a human can verify it, creativity, low inference cost.

## The idea

Build a cascade: parse the drawing when the format is structured, run a small detector trained on Modal when it is a raster, and call a large model (Gemini or Nemotron) only when confidence is low.

In the full dataset, parsing alone covers about 32% of questions and a detector another 67%. The large models then run on a small share of requests, which is what the cost criterion rewards. It also fits what Stoa asks for: a hybrid of geometry, computer vision and small trained models that a better frontier model improves instead of replacing.

## Do first

- **Gemini credits:** fill in the form on the [Best Use of Gemini API](https://hackjunction.app/participate/45/tech-challenges/48) page. It is capped at 400 teams.
- **NVIDIA key:** each of us creates our own API key on NVIDIA Build for the [Best Use of Nemotron](https://hackjunction.app/participate/45/tech-challenges/46) challenge. The free tier allows about 40 requests per minute per key.
- **Modal credits:** follow the [Modal at Junction page](https://modal-com.notion.site/Modal-at-Junction-2026-use-cases-for-every-challenge-4161e7f16949816e9540de36da40a90a).
  - Sign up off the venue Wi-Fi; many sign-ups from one network trip Modal's fraud checks.
  - Each of us redeems the $100 in our own workspace, because a Starter workspace has 3 seats.
  - The code can be redeemed until 16 November 2026.
  - Set the spend limit to $0 so the card is never charged.
  - Before sleeping, run `modal app list` and `modal app stop <app>`.

## What the data says

Numbers are from the full dataset: 127,044 questions over 5,939 drawings, a median of 20 per drawing; 88% are counts and 11% are areas. Scoring: counts must be exact, areas, lengths and volumes within 2%, text answers are free-form.

| Source | Share | Asked | Answer with |
|---|---|---|---|
| cubicasa5k (4,787 PNG rasters) | 67.3% | counts of windows, doors, fixtures, room types (EN, FI) | detector trained on Modal |
| swiss_dwellings (1,040 plans as PDF + DXF + PNG) | 29.6% | counts, room and apartment areas (EN/DE/FR/IT) | DXF parsing; it has DOOR, WINDOW, FIXTURE, ROOM_OUTLINE, UNIT_OUTLINE layers |
| ifc_models (IFC + rendered PDF) | 2.4% | room areas, counts, wall length, slab volume | IfcOpenShell on the model the PDF was rendered from |
| ifc_bench (IFC) | 0.6% | free-form reports | Nemotron agent with IfcOpenShell tools |
| opening_design (3 PDF sheets) | 0.1% | door and finish schedules | PDF table extraction, then Nemotron on the text |
| scans (habs, finna, europeana, wikimedia), DWG, SVG | 0% | no questions | large-model fallback only |

### Where the accuracy points are

| Question group | Share of all questions |
|---|---|
| Swiss counts and areas (parsing) | 29.6% |
| Raster room types: bedrooms, kitchens, bathrooms, WC rooms, saunas, balconies | 24.7% |
| Raster fixtures: toilets, sinks, showers and bathtubs | 22.0% |
| Raster windows and doors | 13.1% |
| Raster floors shown in the image | 7.5% |
| IFC and schedules | 3.1% |

- Always answering the most common count scores about 55% on cubicasa overall, at no cost. That is the floor any model has to beat.
- Windows and doors are the hard group: the same guess scores 14% on windows and 12% on doors.
- 42% of the rasters are under 800 px on the long side, and 7% are over 2,000 px.
- At least one raster is rotated 180°.
- cubicasa uses only 12 question templates, each in English and Finnish, so the template cache covers the query-plan step for most questions with no model call.
- 88% of the ifc_models questions sit on a plan PDF, and some of those ask about the whole model, so always resolve the PDF to its IFC by filename.
- Hand-drawn scans are about 30–45% of the drawings and 0% of the questions. HABS, Finna and Europeana (4,391 scans) are all hand-drawn in a 12-per-source sample; Wikimedia (2,175 scans) is mixed. Most of the hand-drawn sheets are sections, elevations or details, not floor plans.

## How a question gets answered

1. **Question to query plan.** Gemini Flash-Lite with structured output turns a question in any language into `{op, entity, filters, level, unit}`. Cache by template so repeats cost nothing.
2. **Drawing to scene, once per drawing, cached.** Parsers and the detector emit the same JSON: entities with type, label, level, polygon in page coordinates, quantities, confidence and a source reference.
3. **Execute the plan on the scene.** Filter, then count or sum. The matched entity ids are the evidence.
4. **Escalate if confidence is below a threshold, or if the question does not fit one query plan.** Nemotron 3.5 Super VL gets the question, the page image and tools (scene queries, IfcOpenShell code in a Modal Sandbox) and answers with the entities it used. Reconcile and log both answers.

For visual re-counts on rasters, the fallback has to point at items the detector missed. Gemini has documented bounding-box output and Nemotron's is untested, so run both on the 300-question sample and keep whichever scores better.

Every response is `{answer, unit, confidence, evidence[], formula, trace[], cost_usd}`.

Agree on this and the scene schema in the first hour so the four of us can work in parallel.

## Derived questions

Questions such as "how many 4x4 tiles do I need for the toilets?" are not in the dataset, but they are the quantity take-off Stoa cares about and they make the demo. They need three additions, all after the hour-22 gate:

- **Room types across languages (C):** one cached model call per drawing maps its room labels (`TUALETTRUUM`, `WC`, `Bagno`) to a short category list, so "toilet rooms" matches in any language.
- **Formula in the query plan (C):** the plan may carry sub-queries and an arithmetic expression over them, evaluated in code, for example `ceil(area * 1.1 / tile_area)`. The model never does the arithmetic. The Nemotron agent takes what a formula cannot express.
- **Assumptions on the answer card (D):** the reading of the question (toilet bowls or toilet rooms), tile size and unit, waste allowance, floor only or walls too. Show them as editable fields that recalculate.

Limits:

- Areas are exact on Swiss plans and IFC models only. The cubicasa rasters have no scale, so an area there is an estimate at best and must show low confidence.
- Wall tiling needs room height, which only IFC models carry.
- Hand-drawn scans go to the fallback model, and its answers there are unverified.

## Uploaded drawings (the product path)

The dataset gives us sibling files (the Swiss DXF, the IFC behind a plan PDF), and Stoa allows them, so the scored eval keeps using them. A user who uploads a drawing usually has only one file. Each input type gets its own reader; all of them emit the same `Scene`, so the planner, executor, evidence and viewer do not change.

| Upload | Reader | Expected quality |
|---|---|---|
| IFC model | IfcOpenShell | high |
| DXF / DWG | DXF parser | high |
| Vector PDF (exported from CAD) | PDF parser: room labels, numbers and printed areas from the text; walls, door arcs and windows from the vector paths; room polygons from closed outlines | good, given a scale |
| Raster PDF, photo, scan | render to an image, then detector + Gemini, as for cubicasa | medium; areas are estimates |
| Hand drawing | Gemini reads it, per-item checks, amber and "please verify" | low, flagged |

- **The Swiss PDFs are vector**: about 450–540 paths per page, no images, real text such as `Küche 2.02`, `Whg. A` and on some plans `15.61 m²`. So the PDF alone carries most of what the questions need.
- **Scale** is the one hard part, since PDF coordinates are in points. In order: use printed areas directly and calibrate the rest from them; else a scale note or dimension text; else answer counts normally, give areas low confidence and ask the user for one known length.
- **Owner:** A, as `parsers/pdf.py`, after the DXF parser. C helps with the scale calibration.
- **Measure it** by running the Swiss questions with the DXF switched off. That number backs the demo line "upload a PDF, no CAD file needed".
- **API:** `/api/ask` takes dataset paths only today; an upload endpoint is on C's list.

## Viewer and buildings

- **Viewer:** the server renders every format to a page image and the frontend draws an SVG overlay on it, so all formats share one code path.
- **Evidence:** counted items get numbered highlights, with low-confidence ones in amber so the checker looks there first. Areas show the formula, for example `5.08 + 18.91 + … = 83.52 m²`.
- **Buildings:** show each floor's page image as a flat plate in three.js, stacked by level, with the evidence drawn on it. The gap between plates is display-only; no heights are invented (IFC storey elevations are an optional toggle). What can be combined:
  - IFC models: 52 of the 54 with questions have two or more storeys (median 4, max 19), and their plan PDFs link to the model by filename.
  - cubicasa: 963 images show 2–4 floors of one home side by side; split them and stack.
  - Finna: 12 sets have two or more sheets, grouped by the set id in the filename.
- **Not Swiss:** all 1,040 Swiss plans are single floors of different buildings, so they cannot be stacked.

## Cost and confidence scoring

**Cost**

- Each step logs tokens or GPU seconds against a price table into the answer's trace, and `eval.py` writes one JSONL row per question.
- Report the first question on a drawing (includes the scene build) separately from later ones.
- Run plain Gemini Flash, Gemini Pro and Nemotron on the same 300-question sample as baselines.
- Add the free baseline too: always answer the most common count for that question template.
- Price Nemotron at a paid rate (a listed per-token price, or Modal GPU seconds if self-hosted), not at the free tier's $0.

**Confidence**

- **Parsed answers:** high, lowered when the label match is fuzzy or two sources disagree (printed area against polygon area, DXF against PDF).
- **Detector counts:** treat each detection score as a probability and compute the distribution over the count. The answer is the most likely count and the confidence is its probability.
- **Large-model answers:** agreement across 2–3 samples, or with the detector.
- **Calibration:** isotonic regression on held-out questions, so 0.8 means 80% correct.

**Dashboard**

- Accuracy and cost per question by source and tier.
- A cost-against-accuracy chart with the baselines.
- A reliability diagram.
- A threshold slider that replays the logged eval to show how escalation trades cost for accuracy.
- A failure table linking into the viewer.

## Who does what

| Person | Owns | First deliverable |
|---|---|---|
| A | Swiss parser, executor, eval harness; then raster room-type and floor counts; IFC last | Swiss DXF to scene; `eval.py` using the README scoring rules |
| B | detector on Modal: fixtures, windows, doors | CubiCasa5K SVG labels to YOLO format; first training run |
| C | API, Gemini, Nemotron agent, cascade, cost and confidence logging | `/ask` endpoint, query-plan parser, baseline numbers |
| D | viewer, dashboard, 3D, voice add-on, demo | overlay viewer against a mocked scene |

Rasters carry 67% of the questions, so A moves to raster room types and floors as soon as Swiss parsing passes the eval. IFC is 3% of the questions and comes after.

### D's list (14 November)

The overlay viewer, dashboard, illustrative 3D, dictation and read-aloud are done against mocks. Since the judged run is a few hard questions live in our UI, D works in this order:

1. **End to end with real scenes.** Wire the viewer to the first real Swiss or detector scenes as A and B land them. FastAPI serves `frontend-d/` as static files so one public URL serves both.
2. **Correct the model's results in the viewer.** Delete a false positive, draw a missed box, change a type, edit an area, confirm an amber item. The answer recalculates with no model call: the edited scene plus the same plan go through the existing executor. The card shows "corrected by user" with old and new values, and undo. Corrections append to a JSONL file as free labels for B's detector. Editable assumptions (tile size, waste) use the same path.
   - Needs from C: the `QueryPlan` returned inside `Answer`, and `POST /api/recompute {scene, plan}` that runs the executor. Do not port the executor to JS.
3. **Upload flow.** Drag and drop PDF, image, IFC or DXF; show which reader was chosen and the build stages; page picker for multi-page PDFs. Scale calibration: click two points, type a known length. Scans and hand drawings land in the amber "please verify" state, which leads into step 2.
   - Needs from C: an upload endpoint.
4. **Complex-question answers.** One highlight colour per formula variable, the formula with real numbers, Nemotron's tool-call trace on the card (required for the NVIDIA challenge), follow-up questions with a history on the same drawing.
5. **Floor stack in 3D.** Replace the illustrative extrusions with page images as flat plates stacked by level, evidence drawn on each plate, 2D/3D selection sync kept. An explode slider and a solo-floor mode. Alignment: exact for IFC plan PDFs, wall bounding box for split cubicasa floors, drag to align for uploads and Finna sets (the same correction interaction as step 2). Hide 3D for single-page drawings. IFC storey elevations as an optional toggle.
6. **Throughout:** cost and confidence visible on every live answer, abstention and error states that read as deliberate, the demo script and a backup video.

Cut first if time runs short: step 5, then the Gemini-audio voice upgrade (only if browser recognition is poor on the demo machine).

## Models and credits

All large models go through one client: the `openai` SDK with a base URL and model name per provider (Gemini's OpenAI-compatible endpoint, NVIDIA at `https://integrate.api.nvidia.com/v1`, Modal endpoints). Swapping a model is a config change, and token usage is logged the same way for each.

**Gemini: the fast path**

- At request time: the query plan for every question, typed or spoken.
- Offline: nothing for now. Pseudo-labelling the hand-drawn scans for the detector is cut: Stoa tests on the same dataset, and no scan has a question.

**Nemotron 3.5 Super VL: the escalation agent**

- At request time: low-confidence answers, questions that need several steps, free-form IFC questions, and schedules. It works through tools, so its evidence is the entities it queried.
- It is open-weight, so the fallback can run on our own GPUs with no closed API. That supports the cost story for Stoa.
- The challenge requires this exact model in the working solution, with its contribution visible in the demo: show its tool-call trace in the answer card.
- The submission needs a text on what the model does and how we improved its outputs. Report Nemotron on the raw image against Nemotron with scene tools, from the eval.
- Rate limit: about 40 requests per minute per key on the free tier. Use retries with backoff and keep it to the 300-question sample, not the full eval.

**Modal**

- Train the detector; estimated $2–4 per run on an L40S.
- Serve the detector.
- `.map()` over the 5,939 drawings that have questions to prebuild scenes and run the 127k-question eval.
- A Sandbox for the IFC agent's code.
- Deploy the app at a public URL.

Together this also makes a [Best Use of Modal](https://hackjunction.app/participate/45/tech-challenges/40) entry.

**Gemma (lowest priority)**

Stoa, Gemini, Modal and Nemotron are already four submissions. Only if C is ahead at hour 22: fine-tune a small Gemma on question-to-plan pairs to replace the Flash-Lite call, which adds [Best Use of Gemma](https://hackjunction.app/participate/45/tech-challenges/47).

## Voice add-on

Voice only changes how the question arrives and how the answer leaves. `/ask` stays text in, JSON out, so nothing in the backend has to change now. D owns it, after the viewer and dashboard work.

- **Input, first version:** the browser's Web Speech API fills the question box. About 20 lines, no backend, no inference cost. It needs Chrome and a language picker, because it does not detect the language.
- **Output, first version:** the browser's `speechSynthesis` reads a templated sentence: the answer, the unit, the confidence, and how many highlights are uncertain.
- **Upgrade if recognition is poor** on room names, Finnish or venue noise: record the audio and send it to the Gemini query-plan call in place of the text. One call then transcribes and plans, detects the language, and can take the drawing's room labels as hints. Return the transcript so the UI shows what was heard.
- **Check before the upgrade:** Chrome records WebM/Opus; convert it server-side with ffmpeg if Gemini rejects that format.

## Timeline (hours from start)

| Hours | Work | Gate |
|---|---|---|
| 0–2, together | credits, agree on the schemas, read Stoa's answers below | schemas agreed |
| 2–10 | one vertical slice on Swiss | a question shows an overlay in the UI; the eval prints accuracy and cost |
| 10–22 | detector v1 integrated, raster room types and floors, cascade and cost log, dashboard v1 | Swiss and every raster question type go through one API (97% of questions) |
| 22–34 | accuracy push on rasters, calibration, IFC parser, Nemotron escalation agent, derived questions, schedules, 3D view, baselines, voice first version if on schedule | feature freeze at 34 |
| 34–42 | full eval on Modal, fix the largest failure buckets, deploy, polish | public URL works |
| 42–48 | demo script, backup video, submissions to Stoa, Modal, Gemini and NVIDIA | submitted |

## Skipping

- **Retrieval, RAG and vector DBs** (Modal's suggested ColQwen example): every question names its file.
- **DWG, SVG and the scans:** no question in the full set uses them. The working set is the 5,939 drawings named in `questions.jsonl`; nothing else is processed, uploaded to Modal or trained on. Keep about 20 scans that are floor plans as demo material for the fallback.
- **VLM fine-tuning:** slower to iterate and costlier per request than a detector.
- **An OCR engine:** add it only if room-label counts on rasters need it.
- **ElevenLabs or a hosted speech model:** the browser's built-in speech APIs cover the voice add-on.
- **Agent frameworks, auth, a database.** Run logs are JSONL files.

## Stoa's answers (13 November)

1. ~~**Accuracy is judged only on the given dataset.**~~ **Superseded on 14 November (Eino from Stoa, Discord, 11:56):** the final evaluation uses "a few complex drawings and questions", run through whatever interface each team provides, and real questions "can be more complex than the ones in the dataset". Stoa will post a few example questions later that day. The dataset score still matters, but the judged run is a handful of hard, live questions in our UI.
2. **The CubiCasa5K SVG annotations may be used for training, not to answer.** Lane B trains the detector on them. At question time the system sees only the image, and never looks anything up by filename.
3. **Sibling files may be used.** A Swiss PDF question can be answered from the same-name DXF, and an ifc_models plan PDF from the IFC model it was rendered from.
4. **Inference cost is self-reported.** We report it ourselves, so it has to be credible: list prices for every model, and the first question on a drawing shown separately from later ones.

What the 14 November update changes:

- The upload path (any PDF, image or scan), free-form and multi-step questions (formulas, take-offs) and the UI are now judged, not only the demo.
- Scans are no longer demo-only: a complex judged drawing could be a scan (see `blueprints/brain/HANDDRAWN.md`).
- When Stoa's examples arrive, add them to the eval as a separate set.

How we apply answers 1 and 2:

- Every plan Stoa judges on is one we may train on, and `questions.jsonl` already holds every answer. A full-dataset score therefore proves little on its own.
- We develop, tune and calibrate on the 80% train split, and report accuracy on the 20% that `eval.py` holds out as the headline number.
- For the final demo, lane B may retrain the same recipe on all plans. If we quote a full-dataset score for that model, we label it as including training plans.

## Risks

- **IFC areas:** 45% of the IFC area questions are on models with `NetFloorArea`, 39% on models with other area quantities only, and 16% on models with none, which need geometry. Check each path against the gold answers early; the tolerance is 2%.
- **Large IFCs:** 9 of the 54 models with questions are over 50 MB and the largest is 343 MB, so cache the scenes.
- **Nemotron:** its bounding-box output is untested and the free tier is rate-limited, so try both in the first 10 hours before relying on it.
- **Exact counts on rasters:** this is where most of the accuracy score is decided; tune per-class detection thresholds against the question answers.
