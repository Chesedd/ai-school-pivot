# Phase 4B — Human authoring review

## Lifecycle

The generated pipeline checkpoint remains immutable. A completed artifact is copied once into a
separate review aggregate and moves through `reviewing` to either `accepted` or `rejected`.
Starting review is idempotent. Content Bank tasks and versions are created only by the existing
explicit acceptance boundary, after which repeat acceptance returns the original draft identity.

## HTTP contract and DTOs

* `POST /api/content-bank/authoring/sessions/{id}/review` starts or returns review.
* `GET /api/content-bank/authoring/sessions/{id}/review` reads the editable artifact.
* `PUT /api/content-bank/authoring/sessions/{id}/review` replaces it using the supplied `version`.
* `POST /api/content-bank/authoring/sessions/{id}/reject` rejects it.
* `POST /api/content-bank/authoring/sessions/{id}/accept` promotes the latest review version.

`AuthoringReviewDraftV1` contains only title, statement, task type, answer format, choice options,
expected answer, solution, and hints. The same semantic and size bounds as generation apply.
Catalog identity, subject, grade, skills, provider metadata, prompts, and pipeline results cannot be
submitted because the DTO forbids unknown fields. Task type and answer format are also checked
against the frozen request.

## Audit and concurrency

Review audit rows record `review_started`, `review_changed`, `accepted`, and `rejected`, together
with actor, session, and review version. They contain neither prompts nor provider responses.
Edits use compare-and-swap on the review version and return a conflict for stale writers. Acceptance
locks both session and review, consumes the latest stored review, and writes the Content Bank draft,
review acceptance event, and task audit metadata in one transaction.

## Persistence decision

A migration is required: existing session JSON columns are immutable pipeline checkpoints and the
Content Bank audit table requires a task that does not exist before acceptance. Reusing either would
mix review state into the generator pipeline or Content Bank lifecycle. The migration therefore adds
one minimal current-review table and one append-only authoring-review audit table; it does not change
provider, checking, assessment, or Content Bank schemas.

## Next seam

The next backend seam is reviewer assignment/role policy and a dedicated authenticated actor source.
Those concerns are intentionally outside this single-owner review aggregate and require no change to
the artifact or promotion contracts introduced here.
