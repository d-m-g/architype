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
