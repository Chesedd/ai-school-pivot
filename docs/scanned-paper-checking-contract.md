# Scanned-paper checking V1 — architecture contract

## Ownership and identity

`AssessmentScanBatch` owns immutable scan intake (`InputArtifact` originals), the
frozen grading policy, and matching/grouping lifecycle. Any teacher currently
assigned to the assignment's class may act; ownership is not tied to
`Assessment.created_by`. The applicable assessment variant is exclusively
`AssignmentParticipant.assigned_variant_id`; AI neither infers nor changes it.

A future `PaperSubmission` represents one teacher-intaken physical work. It is
not a `StudentSubmission`, consumes no `Assignment.max_attempts`, changes no
digital `attempt_no`, draft, or submission semantics, and is unique per active
participant within a batch (a later batch may contain another). A future unified
assignment-result projection is separate from this contract.

## AI boundaries and review

Page matching and academic checking are separate provider contracts. Matching
receives opaque tokens and only display name (plus explicitly approved aliases)
from roster identity. Checking receives no student identity. Original files stay
immutable; annotations are semantic point/box regions in top-left-origin
`normalized_upright_v1`, never rendered pixels or a “red circle” instruction.

Every batch enters teacher matching review and requires explicit grouping
confirmation, regardless of AI confidence, before checking. The Checking Engine
will later generalize its execution target to `PaperSubmission`; this slice does
not alter `CheckRun`. `checking_completed` means every active paper has a terminal
AI outcome, not that any result is approved or published.

AI outputs are immutable. Future teacher edits create an accepted revision and
never mutate AI evidence. Application code deterministically totals accepted item
scores and maps points/percent to a bounded policy grade label. An explicit grade
override preserves the calculated grade and requires a non-empty reason.

## Publication boundary

AI/check completion can never publish. Publication is an explicit future
per-paper action and is not a batch state. Editing after publication creates a new
revision requiring review, approval, and explicit publication; the prior
publication remains current until superseded. V1 publication is in-platform only
(no email, Telegram, push, or external messaging). Students see only the current
active publication; superseded history remains teacher/audit-visible. The
original multi-student PDF is never student-visible.

## PR 4: AI page-to-student matching

Matching consumes only integrity-verified, normalized `READY` PNG page renders. Its
roster authority is the assignment's immutable set of `AssignmentParticipant`
rows. A run snapshots these as deterministic `roster-0001` tokens and pages as
`page-0001` tokens; the provider receives only a display name and opaque token,
never UUIDs, accounts, storage references, grades, variants, or notes.

Calls are deterministic contiguous chunks of eight primary pages with one adjacent
context page (sequential concurrency of one), using an in-memory 1800-pixel PNG
preview. Provider calls occur outside database transactions. Validated structured
matched, ambiguous, and unmatched results are immutable proposals and telemetry;
retryable incomplete chunks resume the same revision, while succeeded evidence is
never rewritten. Every successful run ends at `MATCHING_REVIEW_REQUIRED`, including
perfect-confidence runs. It neither confirms grouping nor creates a
`PaperSubmission`; teacher-owned revisions remain PR 5 work.

## PR 5 — mandatory teacher page-grouping review

AI page-match proposals and candidates are immutable evidence. Teacher changes are
stored separately in a versioned grouping revision: its entries are identified by
`AssignmentParticipant`, and its page membership and ordering form the human-owned
draft. Ambiguous, unmatched, and invalid matched proposals are persisted explicitly
as unresolved; confirmation requires every ready batch page exactly once and no
unresolved page.

Draft mutations assign, move, unassign, split/combine by participant, and reorder
pages using revision plus row-version optimistic concurrency. A confirmed revision
is immutable; future correction must supersede it with a new revision.

Confirmation locks and revalidates the batch and draft in one database transaction,
freezes canonical participant student and assigned-variant snapshots into one
`PaperSubmission` per group, and copies only ScanPage identities/order into
`PaperSubmissionPage`. It never creates or changes `StudentSubmission`, answers,
digital attempts, or artifact storage. The batch records the `GROUPING_CONFIRMED`
milestone and deterministically reaches `READY_FOR_CHECKING`. No AI/provider or
academic checking call occurs during grouping or confirmation.
