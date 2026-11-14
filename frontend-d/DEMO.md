> Live judging priority: use the editor's drawing intake and live questions first. PDF/PNG/JPEG files can be explicitly uploaded; live analysis needs the backend model provider configured. Do not present a local preview or a successful upload as completed analysis. Rehearse a supported server drawing and an unseen multi-step question when the readers are ready. The ten-step route below is the clearly labelled mock backup.
# D demo flow

Use **Start mock demo** at the top of the workspace. Allow about five minutes. The guide is manual: Previous, Next step, Finish, or Exit. Presenter notes expand in place. Starting it switches to synthetic examples; it does not send API requests. Finish/Exit leaves the last scene or dashboard visible. It does not restore an earlier imported answer or display settings. Sidebar navigation, sample selection and loading a source end the guide. The new steps open the relevant tools but do not automatically apply, reset or undo any draft. Saved mock answers can be opened during the history step without ending the guide; other history navigation ends it.

1. **Count (25s):** six windows with numbered source evidence. Select a window.
2. **Area (25s):** two rooms and the 51.0 m² formula. Quantities are authored mocks.
3. **Uncertainty (20s):** amber door candidate; do not call it a verified count.
4. **Corrections (40s):** the correction panel opens on the door example. Enable draft overlays, select an amber candidate, confirm it, then Undo. If already confirmed in an existing draft, delete an item and undo that deletion instead. Do not clear previous drafts. Show that the reported answer remains unchanged; recomputation is still gated.
5. **Calibration (40s):** the calibration panel opens on the area example. Select two points, or enter page percentages (10, 10) and (60, 10), then save an illustrative 5 m reference. This is an invented demo length, not a measurement. Reset, then Undo calibration. The reported 51.0 m² remains unchanged. Undo the save too if you want to restore the calibration present before this demonstration.
6. **Abstention (15s):** one floor cannot support a whole-building answer.
7. **3D (30s):** orbit and select the same rooms. Heights are illustrative; fall back to 2D if graphics fail.
8. **Voice (20s):** read the answer aloud. Dictation is optional and requires a microphone/browser check. English phrase: “How many windows are on this floor?” Finnish phrase: “Kuinka monta ikkunaa tässä kerroksessa on?” The mock resolver only accepts its exact English sample questions; Finnish transcription is an input test, not proof of multilingual inference.
9. **History (35s):** the answer-history panel opens with the mock drawing selected. Open an earlier mock area answer and inspect its restored 51.0 m² and room evidence. The guide stays open for mock history during this step. Reuse question fills the input without submitting; avoid live/imported history, which exits the mock guide. History is session-only and does not archive image bytes.
10. **Dashboard (35s):** move the threshold slider and open a failure case. Clearly state the values and alternative outcomes are synthetic.

## Before presenting

- Run the D tests and start node frontend-d/dev-server.js.
- Check 3D and sound on the demo machine. English/Finnish microphone recognition is still unverified.
- Keep the mock route as the offline backup. Do not seed backend caches with mock scenes.
- For a live segment, exit the demo, open Sources, check the team API and load a known supported drawing. Confirm the answer, image and evidence before presenting it. API health alone does not verify a builder.
- Import a real evaluation JSONL separately for measured results. Describe its split/model provenance only when supplied by the team.
- Do not claim measured floor heights, floor stacking, calibrated confidence, real benchmark accuracy or inference savings from this mock flow.
