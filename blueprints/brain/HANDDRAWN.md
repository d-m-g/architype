# Annotation plan: hand-drawn floor plans

Status: proposal, 14 November. Owner: lane C, with the whole team for labelling.

**Goal:** a small, trusted test set (about 50 drawings) to measure and calibrate how well we answer on hand drawings, so we can say "when we say confident on hand drawings, we're right X% of the time" and show the evidence. It is **not** for training. Hand drawings carry 0% of the dataset's questions, so this earns no dataset accuracy points: time-box it to about 4 person-hours in total, after the hour-22 gate.

The dataset has 6,566 scans: Finna 1,800, HABS 1,506, Europeana 1,085, Wikimedia 2,175 (all JPG), plus 135 Wikimedia SVGs. Most are sections, elevations or details, not floor plans, so step 1 finds the plans.

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
