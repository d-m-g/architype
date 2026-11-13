# Make Blueprints Talk: hackathon plan

Junction 2026, Stoa challenge, 4 people, 48 hours. Status: proposal, not yet agreed by the team.

- Challenge: https://hackjunction.app/participate/45/challenges/335
- Dataset preview (0.5 GB): https://fwjydgxpttajiqpfmqtj.supabase.co/storage/v1/object/public/stoa-junction-2026/junction-dataset-preview.zip
- Full dataset (10 GB): https://fwjydgxpttajiqpfmqtj.supabase.co/storage/v1/object/public/stoa-junction-2026/junction-dataset.zip
- Judging, 25% each: accuracy, how well the reasoning is visualised so a human can verify it, creativity, low inference cost.

## The idea

Build a cascade: parse the drawing when the format is structured, run a small detector trained on Modal when it is a raster, and call a large model (Gemini or Nemotron) only when confidence is low.

In the preview, parsing alone covers about 43% of questions and a detector another 53%. The large models then run on a small share of requests, which is what the cost criterion rewards. It also fits what Stoa asks for: a hybrid of geometry, computer vision and small trained models that a better frontier model improves instead of replacing.

## Do first

- **Gemini credits:** fill in the form on the [Best Use of Gemini API](https://hackjunction.app/participate/45/tech-challenges/48) page. It is capped at 400 teams.
- **NVIDIA key:** each of us creates our own API key on NVIDIA Build for the [Best Use of Nemotron](https://hackjunction.app/participate/45/tech-challenges/46) challenge. The free tier allows about 40 requests per minute per key.
- **Modal credits:** follow the [Modal at Junction page](https://modal-com.notion.site/Modal-at-Junction-2026-use-cases-for-every-challenge-4161e7f16949816e9540de36da40a90a).
  - Sign up off the venue Wi-Fi; many sign-ups from one network trip Modal's fraud checks.
  - Each of us redeems the $100 in our own workspace, because a Starter workspace has 3 seats.
  - The code can be redeemed until 16 November 2026.
  - Set the spend limit to $0 so the card is never charged.
  - Before sleeping, run `modal app list` and `modal app stop <app>`.

## What the preview data says

There are 7,990 questions over 320 drawings; 76% are counts and 20% are areas. Scoring: counts must be exact, areas, lengths and volumes within 2%, text answers are free-form.

| Source | Share | Asked | Answer with |
|---|---|---|---|
| cubicasa5k (PNG rasters) | 53% | counts of windows, doors, fixtures, room types (EN, FI) | detector trained on Modal |
| swiss_dwellings (PDF + DXF + PNG) | 30% | counts, room and apartment areas (EN/DE/FR/IT) | DXF parsing; it has DOOR, WINDOW, FIXTURE, ROOM_OUTLINE, UNIT_OUTLINE layers |
| ifc_models (IFC + rendered PDF) | 13% | room areas, counts, wall length, slab volume per storey | IfcOpenShell |
| ifc_bench (IFC) | 3% | free-form reports | Nemotron agent with IfcOpenShell tools |
| opening_design (PDF sheets) | 1% | door and finish schedules | PDF table extraction, then Nemotron on the text |
| scans (habs, finna, europeana, wikimedia), DWG | 0% | no questions | large-model fallback only |

The hard part is exact window and door counts on rasters:

- Always guessing the most common answer scores 14% on both.
- 37% of the rasters are under 800 px on the long side.
- At least one raster is rotated 180°.

## How a question gets answered

1. **Question to query plan.** Gemini Flash-Lite with structured output turns a question in any language into `{op, entity, filters, level, unit}`. Cache by template so repeats cost nothing.
2. **Drawing to scene, once per drawing, cached.** Parsers and the detector emit the same JSON: entities with type, label, level, polygon in page coordinates, quantities, confidence and a source reference.
3. **Execute the plan on the scene.** Filter, then count or sum. The matched entity ids are the evidence.
4. **Escalate if confidence is below a threshold, or if the question does not fit one query plan.** Nemotron 3.5 Super VL gets the question, the page image and tools (scene queries, IfcOpenShell code in a Modal Sandbox) and answers with the entities it used. Reconcile and log both answers.

For visual re-counts on rasters, the fallback has to point at items the detector missed. Gemini has documented bounding-box output and Nemotron's is untested, so run both on the 300-question sample and keep whichever scores better.

Every response is `{answer, unit, confidence, evidence[], formula, trace[], cost_usd}`.

Agree on this and the scene schema in the first hour so the four of us can work in parallel.

## Viewer and buildings

- **Viewer:** the server renders every format to a page image and the frontend draws an SVG overlay on it, so all formats share one code path.
- **Evidence:** counted items get numbered highlights, with low-confidence ones in amber so the checker looks there first. Areas show the formula, for example `5.08 + 18.91 + … = 83.52 m²`.
- **Buildings:** extrude room polygons per floor in three.js and stack them by level. IFC models have 3–10 storeys and link to their plan PDFs by filename; Finna sheets group by the set id in the filename.
- **Check first:** every Swiss building in the preview has one floor, so confirm the full set has multi-floor buildings before promising Swiss stacks.

## Cost and confidence scoring

**Cost**

- Each step logs tokens or GPU seconds against a price table into one SQLite table.
- Report the first question on a drawing (includes the scene build) separately from later ones.
- Run plain Gemini Flash, Gemini Pro and Nemotron on the same 300-question sample as baselines.
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
| A | parsers, executor, eval harness | Swiss DXF to scene; `eval.py` using the README scoring rules |
| B | detector on Modal | CubiCasa5K SVG labels to YOLO format; first training run |
| C | API, Gemini, Nemotron agent, cascade, cost and confidence logging | `/ask` endpoint, query-plan parser, baseline numbers |
| D | viewer, dashboard, 3D, voice add-on, demo | overlay viewer against a mocked scene |

## Models and credits

All large models go through one client: the `openai` SDK with a base URL and model name per provider (Gemini's OpenAI-compatible endpoint, NVIDIA at `https://integrate.api.nvidia.com/v1`, Modal endpoints). Swapping a model is a config change, and token usage is logged the same way for each.

**Gemini: the fast path**

- At request time: the query plan for every question, typed or spoken.
- Offline: as a teacher that pseudo-labels non-CubiCasa rasters for the detector.

**Nemotron 3.5 Super VL: the escalation agent**

- At request time: low-confidence answers, questions that need several steps, free-form IFC questions, and schedules. It works through tools, so its evidence is the entities it queried.
- It is open-weight, so the fallback can run on our own GPUs with no closed API. That supports the cost story for Stoa.
- The challenge requires this exact model in the working solution, with its contribution visible in the demo: show its tool-call trace in the answer card.
- The submission needs a text on what the model does and how we improved its outputs. Report Nemotron on the raw image against Nemotron with scene tools, from the eval.
- Rate limit: about 40 requests per minute per key on the free tier. Use retries with backoff and keep it to the 300-question sample, not the full eval.

**Modal**

- Train the detector; estimated $2–4 per run on an L40S.
- Serve the detector.
- `.map()` over all 14k drawings to prebuild scenes and run the 120k-question eval.
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
| 0–2, together | credits, download the full set and rerun the stats, agree on the schemas, fix a train/test split by drawing, ask Stoa the questions below | schemas agreed |
| 2–10 | one vertical slice on Swiss | a question shows an overlay in the UI; the eval prints accuracy and cost |
| 10–22 | IFC parser, detector v1 integrated, cascade and cost log, dashboard v1 | the three main sources go through one API |
| 22–34 | accuracy push, calibration, Nemotron escalation agent, schedules, 3D view, baselines, voice first version if on schedule | feature freeze at 34 |
| 34–42 | full eval on Modal, fix the largest failure buckets, deploy, polish | public URL works |
| 42–48 | demo script, backup video, submissions to Stoa, Modal, Gemini and NVIDIA | submitted |

## Skipping

- **Retrieval, RAG and vector DBs** (Modal's suggested ColQwen example): every question names its file.
- **DWG:** no questions use it.
- **VLM fine-tuning:** slower to iterate and costlier per request than a detector.
- **An OCR engine:** add it only if room-label counts on rasters need it.
- **ElevenLabs or a hosted speech model:** the browser's built-in speech APIs cover the voice add-on.
- **Agent frameworks, auth, anything beyond SQLite.**

## Ask Stoa

1. Is accuracy measured on a hidden question set or from our own report?
2. May we train on the original CubiCasa5K SVG annotations? The rasters come from that public dataset. If not, fall back to Gemini pseudo-labels plus the count answers for threshold tuning. Either way, never look answers up by filename, and hold out plans for honest numbers.
3. How is inference cost measured?

## Risks

- **IFC areas:** three IFC models lack `NetFloorArea` quantities, so compute areas from geometry and check against the gold answers early; the tolerance is 2%.
- **Large IFCs:** the 64–74 MB files parse slowly, so cache the scenes.
- **Nemotron:** its bounding-box output is untested and the free tier is rate-limited, so try both in the first 10 hours before relying on it.
- **Exact counts on rasters:** this is where most of the accuracy score is decided; tune per-class detection thresholds against the question answers.
