# Pre-Phase-4A architecture and regression baseline

Audit date: 2026-08-21. This baseline records the state after the Content Bank UX,
version-workflow, attachment, and import cleanups. It does not start Phase 4A.

## Architecture baseline

1. **Content Bank entities.** `Task` owns internal `TaskVersion` revisions and
   classification/folder links. Revision-scoped data includes skills, typed
   methodology (solution, rubric, accepted answers, typical errors, hints and
   choice rules), managed tags, and task attachments.
2. **Lifecycle.** The supported user lifecycle is `draft -> review -> approved`
   and `review -> draft`; archive makes the card read-only. Approved content is
   immutable. There is deliberately no `approved -> draft` user action.
3. **Internal revisions.** `TaskVersion` and its number remain persistence and
   audit details. The internal clone service still copies content, skills,
   methodology, tags, and attachment links, but no public create-version endpoint
   or user-facing version controls exist.
4. **Assessment boundary.** Assessment authoring selects an approved Content Bank
   revision by `task_version_id`. Publication freezes the assessment composition;
   student execution reads that explicit selection rather than mutable authoring
   state.
5. **Checking Engine boundary.** Assessment creates the documented checking
   handoff from a submitted attempt. Checking consumes its canonical handoff and
   snapshots; it does not import Content Bank presentation or frontend code.
6. **Attachment boundary.** Image blobs are stored behind opaque
   `storage_reference` values. Content Bank attachments belong to task revisions;
   answer attachments belong to saved student answers. They are retrieved beside
   existing content and are not injected into the v1 Checking Engine snapshot.
7. **Intentionally absent workflows.** Legacy task import, user-managed revision
   creation/history, student hint display, and removed folder bulk actions are not
   part of the runtime UI/API.

## Verified audit findings

- The dependency direction remains Content Bank -> Assessment -> Checking handoff.
  Content Bank application code does not import Checking Engine internals, while
  Checking code has no frontend/content-authoring dependency.
- Draft/review/approved transitions and review return retain their existing CAS
  and validation behavior. Approved data and submitted attempts remain read-only.
- Task attachment roles are `statement`, `description`, `additional_material`,
  `solution_explanation`, and `methodological_material`. Uploads enforce draft
  status, configured size, MIME allow-list, and byte signature. Answer uploads
  require an owned saved answer in a draft submission.
- The single answer control sends ordinary text unchanged. The multiple-choice
  newline adapter remains in the student UI boundary; the Checking handoff sees
  canonical assessment answer data, not frontend controls. Attachments remain
  outside the existing answer/checking snapshot.
- Student pages do not render hints. Authoring methodology and persistence retain
  hints, so their established snapshot/fingerprint inputs were not removed.
- Runtime import pages, parsers, services, DTOs, repositories, and endpoints are
  absent. Only the historical create/drop migrations and negative regression test
  remain. OpenAPI contains neither `/imports/*` nor public create-version.
- Alembic has one head, `20260821_02`; attachment migration `20260821_01` follows
  `20260820_01`, and import removal follows the attachment migration.

## Deferred debt and explicit non-goals

- S3/object storage, production attachment RBAC, antivirus, EXIF handling,
  resizing, and other image processing.
- Multimodal Checking Engine inputs. Attachments intentionally do not alter v1
  checking snapshots.
- A product decision and workflow for authoring after approval.
- Multiple-choice label-to-option-ID mapping. The current UI adapter preserves
  labels; a stable ID mapping must be designed before labels become mutable or
  localized.
- PostgreSQL migration execution and the PostgreSQL integration suite must be
  repeated in an environment with PostgreSQL (and Docker, if used for the test
  database). The audit environment provided neither service nor Docker CLI.

No architecture blocker was found for beginning Phase 4A after the PostgreSQL CI
verification gate completes: **PHASE 4A READY**.
