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

Before approving a corrective dataset version, compare each source-level split
for positive-frame prevalence, negative-frame count, image resolution, and
object-size bands. Validation must contain enough independently reviewed tiny
objects to meet the configured checkpoint evidence minimum. It must also include
representative hard-negative footage so threshold calibration can measure false
detections under realistic background prevalence. Add source videos rather than
moving individual frames across splits.

The September 24, 2026 audit of `sportif-ball-v002` found:

| Split | Sources | Positive frames | Negative frames | Tiny objects (<12 px) |
| --- | ---: | ---: | ---: | ---: |
| Train | 2 | 184 | 187 | 24 |
| Validation | 1 | 474 | 223 | 3 |
| Test | 1 | 27 | 138 | 17 |

Validation positive-frame prevalence was `68.0%`, while held-out test prevalence
was `16.4%`. A corrective dataset must be assigned a new dataset ID, receive a
new content hash and approval, preserve the existing test source as held-out
evidence, and add separately sourced, rights-verified training and validation
footage. Do not mutate `sportif-ball-v002`, tune from test labels, or change an
annotation merely because the model predicted a different box.

The approved corrective `sportif-ball-v003` preserves `VID-20260920-001` as the
sole held-out test source. It adds one independently reviewed source to train and
one to validation. The resulting train split has 494 frames, 246 negatives, and
85 tiny objects; validation has 833 frames, 304 negatives, and 56 tiny objects.
Its immutable content SHA-256 is
`f070deeda1df6f43a6f0b3c1e3876815a106ce1fd543170ccec2d1951c9819cb`.

CVAT handoff alone never advances step 5. A human must record the assignee,
review outcome, and export artifact before dataset preparation can be enabled.
