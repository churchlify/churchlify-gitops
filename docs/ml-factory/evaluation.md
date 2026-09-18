# Evaluation

Evaluation targets the source-video-held-out test split. It produces
`metrics.json` with precision, recall, mAP50, mAP50-95, false positives, and
false negatives. Small/medium/large object metrics should be added to the
runtime evaluator before a candidate is considered for production.

The current scaffold deliberately emits null metrics with
`REQUIRES_RUNTIME_EVALUATOR` until inference and IoU matching are run against a
real annotated test set; it never fabricates a passing score.
