# Annotation plan: hand-drawn floor plans

Status: proposal, 14 November. Owner: lane C, with the whole team for labelling.

**Goal:** a small, trusted test set (about 50 drawings) to measure and calibrate how well we answer on hand drawings, so we can say "when we say confident on hand drawings, we're right X% of the time" and show the evidence. It is **not** for training. Hand drawings carry 0% of the dataset's questions, so this earns no dataset accuracy points: time-box it to about 4 person-hours in total, after the hour-22 gate.

The dataset has 6,566 scans: Finna 1,800, HABS 1,506, Europeana 1,085, Wikimedia 2,175 (all JPG), plus 135 Wikimedia SVGs. Most are sections, elevations or details, not floor plans, so step 1 finds the plans.

## How the hand-drawn path works: the model is a sensor, code is the judge

A plain "send the image to a model, show its number" answer is what everyone else will have. Ours differs in five steps:

1. **The model returns structure, not answers**: rooms with labels and boxes or polygons, doors and windows with boxes, the scale bar's end ticks in pixels, any printed areas. Code counts and measures, so every number has boxes to highlight and arithmetic anyone can check.
2. **The drawing checks itself**:
   - **Printed areas** (`35.2 m²`) calibrate the scale and grade every computed area.
   - **The architect's own notes** (a total such as `c/a 360 m²`) check the sum.
   - **Symmetry**: mirrored apartments should have matching counts.
3. **Per-item checks**: crop each candidate door or window and ask "is this a door?". A count becomes many yes/no checks, which models do well.
4. **Room outlines from image processing where the drawing is clean**: a blueprint (white walls on blue) or clean ink can be thresholded and flood-filled into exact room polygons, with no model at all. On rough sketches, ask the model for polygons instead.
5. **Confidence from agreement**: a second model's reading, uncertain items in amber, one-click human confirmation. Confidence is capped until the labelled test set (below) shows it is earned.

Owners: C builds 1–3 and 5 into the escalation agent; 4 suits A or B.

## First tests (14 November, Gemini 4 Carbon, about $0.03 and 80–95 s per drawing)

**1902 ink sketch, Helsingfors** (handwritten Swedish, "Eskiss till boningshus i 4 vån"):
- All labels read correctly, abbreviations included (Kök → kitchen, S.k. → pantry, Sof-rum → bedroom, Barnkammare → nursery); it saw two mirrored apartments.
- Scale bar found (0–20 m): bounding box 26.2 × 15.2 m ≈ 399 m², against the architect's note "c/a 360 m²". The box overshoots the rounded corners, so the gap is expected.
- 25 doors, 20 windows: doors are drawn as short ticks, and the model flagged the door count as uncertain.

**1915 Svenska Teatern blueprint, Eliel Saarinen** (shop floor, printed room areas):

| Room | Printed | From scale bar + box | Gap |
|---|---|---|---|
| Butiker (rectangle) | 560 m² | 505 m² | −10% |
| Butiker (rectangle) | 380 m² | 345 m² | −9% |
| Orchestra foyer | 35.2 m² | 33 m² | −6% |
| Curved offices (wedges) | 11–18 m² | 14–22 m² | +25–60% |

- All 20+ labels read correctly (KAPPRUM → cloakroom, INSPICIENT → stage manager).
- The rectangles are consistently ~9% low: the scale is slightly off, and calibrating on the printed areas fixes it (step 2).
- The curved rooms are off because of boxes, not the model: polygons fix it (step 4).
- 3 doors is clearly wrong (it counted drawn door leaves only): the per-item check (step 3) exists for this.

**Verdict:** the model's reading is strong (labels and scale right on both, even 1902 handwriting); counts and areas need steps 2–5 before they deserve a confidence we'd defend.

## Why this stands out

- **Honesty as a feature:** hand drawings are where AI readers fail quietly. Ours shows which items it is sure of, which it is not, and why.
- **Self-grading demo:** the Saarinen theatre prints its own room areas, so the audience sees our numbers checked against the architect's live, then corrected by calibration.
- **Evidence on a 100-year-old drawing:** numbered highlights on a 1902 sketch, the same overlay as on a CAD plan.
- **Measured, not claimed:** the labelled test set turns "high confidence" into "right X% of the time when confident", with a reliability chart.
- **Demo line:** the same question on a CAD plan (parsed, exact, green), a raster (detector, mostly green) and a 1902 sketch (model + checks, amber where unsure).

## 1. Find candidates (lane C, about 30 min, about $2)

- Gemini Flash looks at a random ~1,500 scans and returns: floor plan or not, hand-drawn or not, legibility 1–3, how many plans are on the sheet, and the label language.
- Pick **60 floor plans**, spread across the four sources and across easy and hard legibility.
- One person skims the 60 thumbnails to drop wrong picks. Keep 50, plus 10 spares.

## 2. What to label (mirrors the dataset's questions)

| Per drawing | Per item (one box each) |
|---|---|
| floors shown on the sheet | door (only with a leaf or swing arc) |
| overall: legible / partly / ambiguous | window |
| | room, plus its type if labelled or obvious: bedroom, kitchen, bathroom, wc_room, balcony |
| | fixture, if drawn: toilet, sink, bathtub, shower |
| | an **unsure** flag on any item |

Counts are derived from the boxes. The boxes also grade the per-item checks and whether the evidence highlights the right things.

## 3. Tool: makesense.ai

It runs in the browser with no install, images stay on your machine, and it exports YOLO or CSV. Label Studio is the fallback if we want a shared server.

Label from scratch, not by correcting Gemini's boxes: pre-filled boxes bias people toward the model, which spoils a test set.

## 4. Rules (agree before starting)

- **Door:** an opening with a door leaf or swing arc. An open passage is not a door. Double doors count as 1.
- **Window:** a window symbol in a wall. Each separately framed unit counts as 1.
- **Room:** an enclosed space. Corridors and stairs are labelled as such.
- **Room type:** set it only if the label or the fixtures make it clear; otherwise "room, untyped".
- **Can't read it:** mark it **unsure**. Never guess; guesses ruin a test set.

## 5. Quality check

- The first 10 drawings are labelled by **two people each**, and their counts are compared.
- Disagreements → 10 minutes of discussion → update the rules above.
- Drawings that are still disputed are marked **ambiguous**: kept for the demo, left out of scoring.
- Report the agreement rate too, for example "humans agree on X% of door counts on hand drawings".

## 6. Who and when (about 4 person-hours)

- Everyone labels about 12 drawings, 5–8 minutes each.
- Best done during waiting time: detector training runs, overnight eval.
- Stop at 40 if behind; that's still enough for a reliability chart.

## 7. Into the eval (lane C, about 45 min)

- A converter turns the labels into `questions.jsonl` using the dataset's own question wordings (English plus Finnish), so `eval.py --data <handdrawn set>` works unchanged.
- Labels are committed as `handdrawn/labels.jsonl`, referencing dataset paths only, never the images.
- Split 25/25: one half to set confidence caps and calibration, the other half for the reported number.

## 8. What it gives us

- Accuracy on hand drawings, overall and "when confident".
- A reliability chart.
- Per-item precision and recall of the AI reader, with and without the crop-and-check step.
- A gallery of the 50 labelled plans for the demo.

## Before using scans in the demo

Each scan source folder has a `.md` file, probably with licence terms. Check them before any scan goes public.
