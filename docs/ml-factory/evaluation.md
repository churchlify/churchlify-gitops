# Evaluation

Evaluation targets the source-video-held-out test split. It produces
`metrics.json` with precision, recall, mAP50, mAP50-95, false positives, and
false negatives. The document separates evaluator execution from model-quality
acceptance with `executionStatus` and `qualityGateStatus`.

The runtime evaluator loads the trained checkpoint, performs inference over the
held-out test video, filters ball detections by the configured score threshold,
and performs one-to-one score-ranked IoU matching. It reports AP at IoU 0.50 and
the mean AP across thresholds 0.50 through 0.95 in 0.05 increments, plus the
precision, recall, false-positive, and false-negative counts at IoU 0.50.

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
Small/medium/large object metrics remain a future release-gate improvement.
