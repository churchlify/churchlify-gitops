# Provenance

Source videos carry a SHA-256 and rights manifest. Dataset manifests record
source videos, video-level splits, seed, annotation format, and validation
status. Model manifests record architecture repository/revision/license,
random initialization, dataset identity, artifact hashes, and the commercial
gate fields.

Stage 4B requires a source provenance object containing `schemaVersion`,
`videoId`, `filename`, `objectKey`, `sha256`, `source`, `sourceRightsStatus`,
`aiTrainingPermission`, `jurisdiction`, and `createdAtUtc`. The workflow refuses
to continue unless rights status is `VERIFIED`, AI training permission is true,
the object key and filename match, and the downloaded video SHA-256 matches.

Successful validation writes `videos/<videoId>/input-validation.json` to the
provenance bucket. Successful extraction writes
`videos/<videoId>/frame-extraction.json` there and writes the JPEG frames plus
`videos/<videoId>/frames-manifest.json` to the frames bucket.

Stage 4C writes `videos/<videoId>/frame-selection.json` to the provenance bucket
and `videos/<videoId>/selected-frames-manifest.json` to the frames bucket. These
documents record the source manifest, algorithm identifier, hash width,
comparison policy, configured threshold, original and selected counts,
per-frame hashes, distances, and selection decisions. Selected entries continue
to reference the SHA-256-verified original JPEG objects.

The September 20, 2026 Stage 4B acceptance run verified that the validation,
frame manifest, and extraction documents shared the same video identity and
source SHA-256; both recorded frame counts matched all 4,724 stored JPEG objects;
and the first and last referenced frame objects were readable. This evidence is
ingest provenance only and does not satisfy later dataset or commercial release
gates.

The September 21, 2026 Stage 4C acceptance run selected 165 of those 4,724
frames at Hamming-distance threshold `8`, rejecting 4,559 near-duplicates.
Independent verification confirmed matching source/video identity across all
four documents, selected-count and decision consistency, valid duplicate
references, matching perceptual hashes, and the SHA-256 of the first and last
selected JPEG objects. This evidence does not imply annotation or dataset
approval.

The CVAT handoff writes `videos/<videoId>/cvat-handoff.json` to the provenance
bucket only after the selected manifest, frame-selection provenance, and every
selected JPEG pass validation and CVAT reports the expected task frame count.
It records the CVAT project/task IDs and names, label schema, selected-frame
count, source identity, assignment state, and explicit false dataset-approval
gate. It does not represent completed annotation, review, export, or dataset
approval.
