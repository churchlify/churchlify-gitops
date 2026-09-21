# Dataset Lifecycle

1. Upload the source video and create its SHA-256 provenance entry.
2. Extract sampled frames at the configured FPS.
3. Select representative frames deterministically and inspect
   `selected-frames-manifest.json` plus `frame-selection.json`.
4. Import only the selected frame objects into CVAT.
5. Annotate only `ball`, review the task, and export YOLO labels.
6. Prepare a dataset manifest with deterministic source-video splits.
7. Run validation and inspect `dataset-validation.json`.
8. Set the approval input to `true` only after human review.

Frames are never split independently. A source video belongs to exactly one of
train, validation, or test. Rights metadata must be verified before a dataset
can pass the commercial eligibility gate.
