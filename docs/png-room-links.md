# Per-room questions on PNG drawings

PNG/JPEG scenes now reuse detected objects, vision room geometry and dark wall pixels to infer room endpoints.
This CPU step adds no model requests. It enriches existing scene caches without rebilling the original detector
or vision extraction. It leaves source-recorded CAD/IFC relationships and original entity scores intact.

The planner and agent can use `where.connected_room_id`, `connected_room_number`, `connected_room_label`,
`connected_room_type`, `connected_room_apartment` and `connected_room_level`. All connection filters must match
one endpoint. Each door/window/fixture counts once, including a door between two rooms with the same label.
Evidence includes the matched objects and requested rooms. Agent entity listings expose connection metadata.

Links have `room_link_status` (`linked`, `partial`, `uncertain`), a reason and unresolved candidate room ids.
Unresolved connections that could change a requested count prevent an exact numeric result. An unrelated
uncertain connection does not prevent another room's count. A partial link already touching the requested room
counts once regardless of its other endpoint. Ordinary whole-drawing counts use their existing path.

This is a conservative heuristic, not a calibrated connection probability. It requires full room coverage and a
single image page. Adjacent swing boxes, overlapping room boxes, missing rooms, underbounded rooms, curved/diagonal
walls and poor scans remain limitations. Global object counts still depend on detector recall. Ranking every room
requires enough resolved connections across every candidate room; this work does not guarantee PNG rankings.

## Offline comparison

Baseline: main `d7c4f26`. Two previously captured PNG scenes (colorful-11397 and colorful-11400), 13 visually
labelled per-room door-count tasks each. These development samples are not a held-out benchmark.

- 12 correct numeric results, 0 wrong numeric results, 14 abstentions out of 26 tasks.
- Whole-drawing door/window/toilet/room count and evidence regressions: 0.
- Incremental model calls/cost during comparison: 0 / $0.
- This tests scene enrichment and execution, not natural-language interpretation or live end-to-end accuracy.
- The baseline direct attribute filter returned zero without relationship metadata; this comparison does not
  measure the baseline agent's visual fallback.

Reproduce with saved audit captures (not committed drawing data):

```powershell
uv run --extra brain python tools/compare_png_room_links.py <audit-directory> --baseline d7c4f26
uv run --extra brain python -m unittest blueprints.test_image_room_links
```

Existing extraction/agent model costs still apply as before. Previously cached, filtered PNG object-question
plans are refreshed once into a separate room-connection cache; unfiltered count caches are reused.

## Vision fallback

When unresolved connections block a room-filtered query, `execute_with_links` asks the existing vision model
about uncertain objects of that type. The agent can also call `resolve_room_connections` before a ranking.
Each call batches at most 24 objects and there are at most three attempts per drawing/geometry/model revision.
Positive and negative decisions are cached; failed provider requests have a five-minute cooldown. A request
has a 60-second timeout, no automatic retries, low reasoning effort and an 8192-token generation cap. Costs and actual token usage appear
in `resolve_room_connections` trace steps; provider failures without usage cannot be priced reliably.

The model sees the original image and an annotated overview plus existing room ids and object boxes. Accepted
links must reference actual rooms and provide nearby endpoint coordinates within the assigned room boxes.
Ambiguous, unmapped, duplicate, missing or invalid model decisions leave the original uncertainty intact.
For a door/window, two endpoints assigned to the same room cannot remove uncertainty about another nearby
room. Such decisions are rejected. Cached positive decisions are revalidated, so a stricter validation policy
can restore the geometry's original uncertainty without making another model request.
These checks validate references/geometry, not the model's interpretation of a wall: model links are labelled
as model-inferred and not independently verified. Original object scores are retained. Cached decisions are
reapplied on scene loads without rebilling their original cost. `/api/recompute` remains model-free.

The OpenAI-compatible provider can spend generation tokens on thinking before emitting JSON; low reasoning
effort and the generation allowance prevent truncated link records. Billing uses the greater of completion
tokens and total tokens minus prompt tokens, so reported thinking tokens are included. See
[Google's reasoning controls](https://aistudio.google.com/docs/openai#thinking).
An exact room-filtered count still unresolved after refinement returns its evidence gap directly rather than
paying for another agent to try the same unverifiable count. Unsupported question types still use the agent.

The 12/26 result above is the **CPU baseline**, not a measured result for this fallback. Real-model results
must be reported separately, including incorrect answers, abstentions and model/agent costs.

## Real-model development result (two PNGs)

Two live vision batches generated review records for the same saved images. Replaying those records through
the final validator produced **19 correct counts, 0 incorrect counts, 7 abstentions** across 26 per-room tasks,
compared with the CPU baseline's 12 correct / 14 abstentions. This is 73.1% answer coverage on development
samples, not a held-out accuracy estimate or evidence that all PNG drawings work.

Those two batches cost $0.011559 in the app's token-price accounting and took 7.45 / 5.32 seconds. A repeated
successful question returned the same answer for $0, with no additional refinement call. An earlier validator
accepted a same-room door assignment and caused one false zero; the stricter validator rejects that decision.
The final policy was evaluated using cached real-model responses, avoiding another generation charge.

`python tools/benchmark_room_link_vision_live.py <audit-directory>` runs one real pilot question per PNG,
then scores all 26 room queries locally from the resulting scenes. It prompts for the demo password with
`getpass`, keeps cookies in memory, writes no credentials, and stops at the first sample with wrong counts.
