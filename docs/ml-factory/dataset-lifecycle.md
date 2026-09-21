# Dataset Lifecycle

1. Upload the source video and create its SHA-256 provenance entry.
2. Extract sampled frames at the configured FPS.
3. Select representative frames deterministically and inspect
   `selected-frames-manifest.json` plus `frame-selection.json`.
4. Import only the selected frame objects into CVAT.
5. Assign the imported task to an annotator and a distinct reviewer when
   staffing permits. Annotate only `ball`, complete review, and export YOLO 1.1.
6. Prepare a dataset manifest with deterministic source-video splits.
7. Run validation and inspect `dataset-validation.json`.
8. Set the approval input to `true` only after human review.

Frames are never split independently. A source video belongs to exactly one of
train, validation, or test. Rights metadata must be verified before a dataset
can pass the commercial eligibility gate.

CVAT handoff alone never advances step 5. A human must record the assignee,
review outcome, and export artifact before dataset preparation can be enabled.
