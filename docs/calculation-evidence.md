# Raster calculation evidence

The provisional cutoff is **60%**, requested for this demo. It is a threshold, not a cap on the answer's score.
It is not a statistically calibrated probability that an answer is correct.

For PNG/JPEG object counts:

1. Exclude confirmed legend symbols, duplicates and detections below 60%.
2. Select the requested object class and room endpoints, counting each object once.
3. Compute the geometric mean of included detection scores and bound it by the lowest requested-room score.
4. Multiply by `included / (included + matching detections below cutoff)` to account for cutoff coverage.
5. Use the lowest query score for calculations with multiple queries; refuse a numeric result below 60%.

An actual unresolved connection that could change a room count still prevents an exact total. Known matches
remain visible when a total is unavailable. This score describes calculation support from extracted evidence;
arithmetic is checked separately by the executor. It does not establish detector recall or validate question
interpretation. Counts use qualifying detections, so low-score omissions must remain visible to the user.
Original CAD queries keep their existing evidence policy. No shared schema change is needed.

The `calculation_evidence` trace step contains structured query-level included ids, context room ids, omitted
ids with reasons, unresolved ids, coverage, score and cutoff. The UI numbers only counted objects; supporting
rooms are not counted as doors. Omitted and unresolved detections are inspectable in separate disclosures.
Selecting a row highlights and zooms to its source, including a symbol outside the plan. The UI refreshes the
scene after an audited answer so cached type corrections and badge totals match the calculation.

## Cached-review migration

New vision reviews distinguish `observed_type`, `classification_clear` and `non_object_kind` from room-endpoint
certainty. A legend outside the floor-plan bounds can be excluded even when its endpoint `clear` is false.

Older reviews are migrated conservatively: the exact explanation "Legend item outside floor plan" plus
geometry clearly outside all room/level bounds excludes a legend. An explanation beginning "Exterior window
on" or "Exterior window/opening" corrects a door/window classification. Other mentions of windows or legends
do not change classification. This is a compatibility bridge for already cached reviews, not a general text
classifier. Original types/scores are retained for audit; corrected types use the original type in the model
cache key, avoiding a new model request. Overlapping corrected windows are deduplicated against a higher-score
window at IoU >= 0.5. Relabelling does not manufacture a new calibrated classification score.

Regression drawing: `uploads/41fb89089508fe7150e3.png`. The cached-review replay produces seven included doors
for Couloir 4.05: `det-0`, `det-2`, `det-8`, `det-14`, `det-16`, `det-19`, `det-25`; support score 70%, cutoff 60%.
`det-35` is an excluded legend, and `det-31`, `det-55`, `det-56` are corrected from doors to windows.
The replay requires no new vision request. `tools/verify_calculation_evidence_live.py` verifies the same result
on the demo with credentials held in memory only.
