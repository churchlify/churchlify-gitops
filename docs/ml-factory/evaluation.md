# Evaluation

Evaluation targets the source-video-held-out test split. It produces
`metrics.json` with precision, recall, mAP50, mAP50-95, false positives, and
false negatives. Small/medium/large object metrics should be added to the
runtime evaluator before a candidate is considered for production.

The runtime evaluator loads the trained checkpoint, performs inference over the
held-out test video, filters ball detections by the configured score threshold,
and performs one-to-one score-ranked IoU matching. It reports AP at IoU 0.50 and
the mean AP across thresholds 0.50 through 0.95 in 0.05 increments, plus the
precision, recall, false-positive, and false-negative counts at IoU 0.50.

These engineering metrics are recorded without an automatic quality threshold.
They must be reviewed before a candidate can be promoted. Small/medium/large
object metrics remain a future release-gate improvement.
