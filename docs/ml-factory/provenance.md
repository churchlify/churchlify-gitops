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
