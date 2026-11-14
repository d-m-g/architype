# Swiss reader verification - 14 November 2026

Scope: all 2,358 Swiss Dwellings question records in the supplied preview, covering 65 distinct floor plans. Translations are separate records; this is an all-split development audit, not a held-out accuracy claim.

## Operational checks

- 65/65 floor plans parsed successfully using the team's unchanged `swiss_dxf@5` reader.
- 65/65 source PNGs exist and match reported page dimensions.
- Every entity has a valid page and normalized coordinates (validated by Scene).
- Every answer's evidence IDs resolve to its source scene; no missing evidence IDs.
- All 2,358 questions returned answers through the deterministic cached-plan/executor path, with external model calls disabled.
- The local HTTP/UI smoke check is run separately before pushing.

## Reference scoring

| Task | Correct / records | Reference score |
|---|---:|---:|
| Area | 744 / 744 | 100.00% |
| Count | 1464 / 1614 | 90.71% |
| All | 2208 / 2358 | 93.64% |

The 150 reference disagreements are entirely explained by the documented duplicate-counting policy: 122 door records and 28 window records. For every disagreement, the reference equals the sum of the reader's `attrs.copies`, while the returned answer counts each unique object once. There are no unexplained disagreements in this preview under that audit.

This does not make the raw reference score 100%. The team must retain or reconcile the confirmed physical-object counting rule with the preview labels before presenting benchmark scores. The frontend must not silently replace the reader's result with a reference label.

## Limits

Only the provided preview is available locally; this does not certify the full challenge dataset. Model-backed vision/Nemotron paths and authenticated live deployment require server configuration. No parser, executor, schema or backend file was edited for this verification.

## Reproduction

Set `BLUEPRINTS_CACHE` to a fresh directory under `frontend-d/.cache/`. Disable external model keys for this offline check. Run the existing `eval.py` with `--data <preview root> --source swiss_dwellings --split all --out frontend-d/.cache/swiss-dark-mode-verification.jsonl`. Keep reference-bearing JSONL and scene caches out of commits and runtime retrieval.
