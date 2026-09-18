# Provenance

Source videos carry a SHA-256 and rights manifest. Dataset manifests record
source videos, video-level splits, seed, annotation format, and validation
status. Model manifests record architecture repository/revision/license,
random initialization, dataset identity, artifact hashes, and the commercial
gate fields.

Use `python -m sportif_ml.provenance validate-input OBJECT_KEY` before frame
extraction and `python -m sportif_ml.provenance release DATASET_ID` only after
all mandatory artifacts exist.
