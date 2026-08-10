# Checking intake Phase 4.2 verification

## Boundary and transaction

`CheckingIntakeService` owns the transaction. Its UoW first locks the exact
`student_submissions` row, verifies the submitted state and timestamp, reads the
assessment composition, stored answers, exact historical task versions and typed
methodology, then calls the existing `CheckingRepository.create_run`. Both the run
and `run_created` event are flushed before the UoW commits. Neither adapter commits.
The repository's existing source-row lock, uniqueness constraints and active-run
index remain the concurrency authority; there are no process-local locks.

## Immutable allowlisted schema

`checking_input_v1` contains only `snapshot_schema_version`, `handoff_version`,
`routing_contract_version`, `source_contract_versions`, `submission_id`,
`submitted_at`, and `items`. Items contain the Handoff v1 fields plus
`methodology`, `rubric_item_ids`, `typical_error_ids`, and `skill_ids`.
Methodology includes statement/type/format, skills, expected solution and authored
steps, typed and legacy accepted answers, choice catalogue and scoring policy,
rubric/items, and typical errors. Lifecycle, authorship, assignment, participant,
student, class/group, audit, tag, folder and timestamp metadata are not copied.
Raw and normalized answers are forwarded exactly as stored and never normalized.

## Canonical bytes and identities

Canonical JSON is UTF-8 with non-ASCII retained, sorted keys and compact
separators. UUIDs are lowercase; timestamps are UTC `Z`; JSON booleans/null stay
native. Decimals are plain, exponent-free strings with signed zero mapped to `0`;
methodology trailing zeroes are removed while frozen points retain two places.
Authored step order and arbitrary student arrays are preserved. Items, rubric
items, options, accepted answers/memberships, skills, errors and scoring rules use
the documented semantic ordering.

`input_fingerprint = sha256(canonical_snapshot_utf8_bytes).hexdigest()`.
The canonical run request contains submission/fingerprint, snapshot schema,
routing, checker-set, threshold-policy and prompt/model-policy versions, plus the
nullable superseded run. `request_hash =
sha256(canonical_run_request_utf8_bytes).hexdigest()`. Same-key/same-hash replays;
same-key/different-hash conflicts. Explicit reruns use a new key and a terminal
same-submission predecessor, while attempt number remains history only.

## Historical behavior and gate

No current approval/archive or assignment-open state is consulted: the frozen
assessment item points and referenced task version remain authoritative after
archive, close, or later version authoring. Existing persisted snapshots are not
mutated.

Run focused units and the real PostgreSQL module against a disposable URL ending
in `_test`, followed by existing Checking/Handoff/Phase 3/typed methodology
regressions and `alembic current`, `heads`, and `check`.

## Non-goals

There is no routing decision, checker, scoring/verdict derivation, provider/prompt
execution, worker, retry lease, public endpoint, frontend, Student Attempt change,
normalization change, Teacher Review, analytics, recommendation, or IAM work.
There is no schema or Alembic revision.
