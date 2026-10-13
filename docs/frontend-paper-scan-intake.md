# Scanned-paper frontend intake (PR1)

This frontend slice starts at a teacher assignment, supports multiple scan batches,
uploads sources, runs/resumes extraction, and ends at a matching route placeholder.
It does not launch matching, grouping, checking, grading, or publication.

Base: `74a45a210b5996293ef24d35534da21a0d3e095f` (`main`).
Branch: `codex/paper-scan-intake-ui`. Suggested PR title: **Add scanned paper intake UI**.
The change is prepared locally; pushing and merging are outside this task.

## Architecture and routes

The existing stack is React, TypeScript and Vite, with handwritten HTTP types,
the shared credentialed `request`/`ApiError` client, capability-based authentication,
and pathname routing using browser history. Data loading uses React effects and
state rather than a query library. Tests use Vitest, jsdom and Testing Library.
There is no generated API client, MSW, ESLint configuration/script, or browser-test
framework. The UI reuses semantic CSS tokens, buttons, cards, alerts, and native
forms. It adds no dependencies, modal system, toast system, or icon library.

Entry: **Проверить бумажные работы** on the class assignment row and assignment
results header, visible with `assessment.scan_check.manage`. All three routes
require that capability; the backend still decides class access:

- `/classrooms/:classId/assignments/:assignmentId/paper`: all historical batches
  and new-batch form.
- `/classrooms/:classId/assignments/:assignmentId/paper/:batchId`: intake.
- `/classrooms/:classId/assignments/:assignmentId/paper/:batchId/matching`: bounded,
  nonfunctional handoff shell for PR2. It makes no matching request.

Assignment/class and batch/assignment identities are checked against server data
before displaying the corresponding resource. No teacher enters UUIDs or student
IDs. Back, forward, reload and direct links reconstruct academic state from the API.
DTOs are kept only in memory; no localStorage or IndexedDB persistence is added.

## Backend contract used

The authoritative implementation was inspected in `scan_intake_routes.py`,
`scan_intake_schemas.py`, `scan_upload_form.py`, `scan_intake.py`,
`scan_page_pipeline.py`, `scan_page_rasterizer.py`, `input_artifacts.py`,
`scan_matching_service.py`, and `scan_storage_content.py`. Existing handwritten
frontend typing conventions are retained; response fields match those HTTP routes.

All paths below are relative to `/api/assessment-core`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/assignments/:assignmentId` | Authoritative assignment and participants |
| GET | `/assessments/:assessmentId` | Assignment's assessment title |
| GET, POST | `/assignments/:assignmentId/scan-batches` | All batches; create batch |
| GET | `/scan-batches/:batchId` | Batch metadata |
| GET, POST | `/scan-batches/:batchId/artifacts` | Source metadata; one-file upload |
| GET | `/scan-batches/:batchId/pages` | Extracted page metadata only |
| POST | `/scan-batches/:batchId/extract` | Explicit extraction and safe resumption |
| GET | `/scan-batches/:batchId/artifacts/:artifactId/content` | Requested source download |

The matching boundary's POST `/scan-batches/:batchId/match` and GET
`/scan-batches/:batchId/matching` were inspected but are **not called** by PR1.

Creation requires nonempty trimmed `instruction` (maximum 10,000 characters) and
an `Idempotency-Key`. A synchronous mutation guard prevents duplicate submissions;
an unchanged instruction retains its key after an uncertain response. The server's
returned batch ID determines navigation. Failures refetch the available batches.
Editing the instruction deliberately starts a different command.

The source upload accepts exactly one multipart `file` and `upload_position`.
Supported signatures and MIME types are PDF, JPEG, PNG and WebP. Multi-selection
is implemented with sequential requests, assigning positions after the greatest
existing upload position. No count, byte or page limit is hardcoded in the client:
those limits are deployment configuration and remain authoritative on the server.
The picker uses the accepted MIME types; drag/drop also remains server-validated.
The client checks empty selection and the instruction contract, and does not alter
or truncate file contents.

Every selected file has an explicit selected/uploading/uploaded/unconfirmed/not-sent
outcome. Upload stops on the first rejection or uncertain acknowledgement, retaining
successful sources and identifying every unattempted file. The teacher checks the
server list and reselects remaining files; uploads are never automatically retried.
No upload idempotency contract exists. Selection must be resolved or explicitly
cleared before starting extraction. There are no fabricated progress percentages.

## Processing, errors and readiness

Uploads are legal only in `draft`/`uploading`. Extraction is explicitly started
after uploads; once started, the batch no longer accepts sources. In `extracting`,
the same extraction command resumes `pending`, `running`, and `failed_retryable`
sources, preserving completed sources and skipping `failed_terminal` sources.
There is no invented per-file retry endpoint. Terminal failures and later batch
states offer no intake mutation.

Artifact states map to waiting, processing, completed, retryable failure, and
terminal failure. Batch states map to restrained Russian labels for intake and
later stages; unknown states do not enable mutations. Failed-page counts are
attributed to their source file. Other successful sources remain visible.

`usePaperScanBatch` serializes refresh cycles, using parallel metadata reads within
each cycle. It refetches after mutations, during the extraction request, and every
three seconds while server artifacts are pending/running in `extracting`. Stable
success/failure stops polling. Hidden tabs pause polling; focus/visibility return
refetches. Cleanup aborts reads and clears timers. Failed reads remove stale
actionable metadata until a successful refresh.

Central error mapping uses `ApiError.code`, retaining machine codes for diagnostics
without displaying raw messages. It distinguishes format, size, source/byte/page
capacity, extraction/storage failures, concurrency/idempotency, validation and
authorization. Unknown 5xx errors use bounded server feedback. Mutation failures
refetch state. No raw exception, storage reference, provider data, file bytes, or
access token is logged or shown.

Downloads occur only on explicit request through the authorized API, using the
shared client with an optional binary response mode. A 410
`scan_artifact_content_purged` displays **Исходный файл больше не хранится** in that
row while preserving metadata. There is no restoration functionality, automatic
source download, thumbnail loading, or direct storage access.

The handoff is enabled only with a nonempty assignment roster, batch `extracting`,
all sources completed, a nonempty page list, page counts matching source metadata,
and every page `ready` with a fingerprint and content URL. Pending selection,
mutations, refreshes, and read errors block handoff. It navigates only to the PR2
placeholder. Already progressed batches render read-only intake history and an
informational link to that shell; current checking/publication-stage routes do not
exist in this slice.

**Public contract limitations:** no `can_match`/readiness DTO is exposed. Page
metadata does not expose the derived artifact integrity and coordinate-space
checks performed by the matching command, and deployment capacity may change.
PR1 therefore checks all exposed intake prerequisites without pretending to start
matching; PR2 must trust/refetch after the authoritative command. No backend change
was made to provide these fields. There is no upload idempotency API, public limits
configuration, per-file extraction retry, or terminal-file replacement in a party
that has already started extraction. The UI does not invent those capabilities.

## Validation and acceptance

Focused behavior tests cover historical batches, creation and duplicate guards,
idempotency after uncertain responses, full multi-selection, partial uploads,
validation errors, processing/resumption, terminal failures, page failures,
readiness, reload, progressed history, safe errors, purged content, focus/hidden
polling, nonoverlap, active-command refresh, cleanup and route capability guards.
Fixtures retain the actual batch/artifact/page HTTP shapes. No browser framework
was added.

Commands supported by the repository:

- `npm run test:run` (Vitest).
- `npm run test:run -- --maxWorkers=2` for reliable broad runs on this host.
- `npm run build` (`tsc -b` then the Vite production build).
- `git diff --check` for whitespace.

There is no separate typecheck or lint script. Typechecking is performed by the
build; ESLint cannot be reported as run. The default broad run's 5-second timeout
in `TeacherRemediationBuilderPage.test.tsx` (the suggested/manual lookup and 20-item
limit test) was reproduced from the exact base archived into an isolated temporary
directory with the same installed dependencies. It is not classified as baseline
merely because its source is unchanged. A two-worker broad run passes.

Manual browser acceptance used disposable, contract-shaped HTTP fixtures, not real
academic data: class assignment entry → create → select two PDF files → upload →
explicit extraction → processing → two-source/two-page summary → matching shell →
back → reload. Also checked mixed success/retryable failure, retained successful
rows, disabled handoff, legal resume action, and intentional 410 feedback. These
checks verify frontend behavior; they are not a live-backend integration run.

Keyboard-accessible native file picker, labeled instruction form, actual links,
live processing status, textual errors and nearby disabled-action explanations
are used. Drag/drop supplements the picker. Visual checks at desktop 1280px and
760px show wrapped long filenames, readable failure states and no horizontal
overflow. Viewport overrides were reset after inspection. Screenshots and the
final commit/file/validation inventory are recorded in the local completion report.

Backend regression check:
`git diff 74a45a210b5996293ef24d35534da21a0d3e095f -- backend` must remain empty.
No backend files, migrations, tests or behavior are changed.
