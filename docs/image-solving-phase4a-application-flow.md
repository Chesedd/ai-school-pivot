# Image Solving — Phase 4A application flow

The `ImageSolvingSession` aggregate is separate from authoring generation. It
owns the artifact identity and the extraction, solver, and deterministic
validation checkpoints. Its lifecycle is `created`, `extracting`, `extracted`,
`solving`, `solved`, `validated`, or `failed`.

`ImageSolvingService` verifies owner access and a storage-computed SHA-256 before
running an extractor. The solver receives only `SolverInputV1`: it never receives
raw bytes, storage references, provider credentials, or hidden artifact metadata.
The extractor's closed output schema cannot contain answers, solutions, or hints.
Validation uses fixed confidence, OCR, solver-status, and answer-consistency
checks and never calls a model provider.

Resume is checkpoint-driven: extraction is skipped when it is durable; solving
is skipped when its checkpoint is durable; a durable validation is returned
without any provider call. Invalid ordering or fingerprint corruption fails
closed. A compare-and-set running lease prevents concurrent execution, while a
lease abandoned for five minutes can be recovered.

Persistence is isolated in `image_solving_sessions` and
`image_solving_checkpoints`. It does not read or write `tasks`, `task_versions`,
or `authoring_reviews`. Existing authoring sessions remain unchanged. The older
extractor-to-`generator` terminology mapping remains only in the legacy
`AuthoringRepository`-backed extraction adapter; it is not used by this flow.
