# Phase 4E — Authoring Workspace read model

`GET /api/content-bank/authoring/sessions/{id}/workspace` returns the complete,
versioned `authoring_workspace_view.v1` projection needed by a reviewer. Its
sections are session, generation, solver, review, quality, acceptance, and
diagnostics.

## Query strategy

The dedicated `AuthoringWorkspaceRepository` first applies both session ID and
owner ID to the root query. It then performs fixed, bulk collection reads for
attempts, the single review and its revisions/audits, and the optional promotion
audit. The complete view therefore uses at most six queries. Query count is
bounded and does not grow with the number of attempts
or revisions (no N+1); separate select-in-style reads also avoid a cartesian join
between those collections. No query locks rows and the endpoint never commits,
runs a provider, or invokes the pipeline.

## Security boundary

The DTO is an explicit allowlist. It exposes route identity, statuses, aggregate
usage/cost, deterministic validation and quality findings. It does **not** expose
prompt/settings snapshots, provider responses or response identifiers, secrets,
the generated task body, solver proposed answer/reasoning, internal revision
snapshots, or audit details other than the reviewer-entered warning override.
An unknown or foreign-owned session has the same `404` response.
