# AI Content Authoring — Phase 4A.1 implementation

Phase 4A.1 adds a Content Bank-owned execution foundation only. The immutable
`AuthoringRequestV1` is strictly validated, frozen with its resolved catalog
allowlist, serialized as deterministic canonical JSON, and identified by a
SHA-256 fingerprint. User brief and source content remain untrusted data and
are never included in technical errors or attempt logs.

Generator and solver are execution roles, not implemented authoring semantics.
Immutable prompt specifications identify stable name, role, semantic/template
versions and hash, output schema, and policy. The provider-neutral port receives
only immutable provider/model/settings, prompt identity, fingerprint, bounded
timeout/retry policy, correlation identity, and idempotency key. The fake
provider returns only a deterministic technical contract probe.

`authoring_sessions` owns immutable author metadata, frozen request/allowlist,
fingerprint, logical status, and revision. `authoring_provider_attempts` owns
numbered generator/solver history, prompt/settings snapshots, lifecycle,
bounded failure code, response hash, token usage, and exact `NUMERIC(18,8)`
cost metadata. A session row lock serializes attempt numbering; session/key and
session/role/number uniqueness make replay deterministic. Same key plus another
fingerprint is a typed conflict. CAS transitions claim only `pending` attempts
and finalize only `running` attempts, so terminal history is immutable. Retries
create new rows and are bounded to five by the application contract.

Failures are restricted to application-owned codes. Timeout, rate limit,
transient transport, and provider 5xx are retryable; authentication, invalid
provider request, unsupported configuration, content blocking, and contract
violations are terminal. Adapter exception prose and unrestricted raw provider
responses are not persisted. Usage values are bounded non-negative integers;
cost accepts finite, non-negative canonical plain `Decimal` values with at most
eight fractional digits, currency, pricing version, and source.

## Explicitly deferred to Phase 4A.2+

There is no `GeneratedTaskDraftV1`, task generation, independent solving or
comparison, methodology generation, preview/revisions, authoring REST API,
frontend, confirmation, Content Bank Task/TaskVersion creation, automatic
review/approval, or real-provider quality acceptance. Session states required
by the normative contract exist for forward-compatible persistence, but this
slice does not simulate `ready` or `confirmed` transitions.
