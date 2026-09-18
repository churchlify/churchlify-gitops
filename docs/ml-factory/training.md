# Training

The initial configuration is 150 epochs, batch size 16, image size 1024, seed
42, and one GPU. These are defaults, not claims of optimality. The trainer
verifies dataset validation, sets deterministic seeds, requests CUDA when
available, and constructs the model with both `weights=None` and
`weights_backbone=None`.

The model source and license are recorded in `model-manifest.json`. Dependency
and license inventory must be completed before release; code license and model
weights are treated as separate provenance facts.
