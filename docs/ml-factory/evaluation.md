# Evaluation

Evaluation targets the source-video-held-out test split. It produces
`metrics.json` with precision, recall, mAP50, mAP50-95, false positives, and
false negatives. The document separates evaluator execution from model-quality
acceptance with `executionStatus` and `qualityGateStatus`.

The runtime evaluator loads the trained checkpoint, performs inference over the
held-out test video, filters ball detections by the operating threshold selected
only from the validation split during training, and performs one-to-one
score-ranked IoU matching. It reports AP at IoU 0.50 and
the mean AP across thresholds 0.50 through 0.95 in 0.05 increments, plus the
precision, recall, false-positive, and false-negative counts at IoU 0.50.
`metrics.json` records `scoreThresholdSelection: validation` so threshold
provenance is explicit and test-set tuning cannot be mistaken for evaluation.

The release-producing workflow fails closed unless all configured minimums pass:

| Metric | Minimum |
| --- | ---: |
| mAP50 | 0.50 |
| mAP50-95 | 0.20 |
| Precision | 0.60 |
| Recall | 0.60 |

The evaluator writes the complete failed result before exiting non-zero so the
run remains diagnosable. ONNX export, provenance creation, and candidate
publication cannot run after a quality failure. Provenance independently checks
the quality decision to prevent downstream invocation from bypassing the gate.

Validation threshold calibration is also fail closed. A validation operating
point is checkpoint-eligible only when it satisfies all of the configured
minimum precision and recall constraints and the maximum detections-per-negative-
frame constraint. The threshold sweep is retained for diagnosis when no point is
eligible, but the least-bad fallback cannot select a publishable checkpoint.
Checkpoint eligibility additionally requires the configured minimum number of
tiny-object validation annotations; a high recall calculated from a single tiny
annotation is not sufficient evidence.

Evaluation reports positive and negative frame counts, detections on each frame
class, and detections per negative frame. These values expose prevalence shift
that aggregate F1 alone can conceal.

## September 24, 2026 v002 investigation

Workflow `sportif-ball-training-v002-20260924` completed training but failed the
held-out quality gate. Its selected epoch-10 checkpoint had validation precision
`0.0380`, recall `0.0549`, mAP50 `0.0029`, and only one tiny validation
annotation. No threshold satisfied the then-configured validation recall floor;
the previous calibration implementation nevertheless retained a diagnostic
fallback threshold of `0.45` as the operating threshold.

The held-out test split contained 27 positive and 138 negative frames. At the
validation-selected threshold, the model produced 436 detections on negative
frames and failed every release metric. The test set must remain held out: do not
select a replacement threshold from these results.
