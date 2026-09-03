# Post-J1 human-confirmed catalog aliases

J1 remains complete. This enhancement is a lookup-memory feature, not catalog
MERGE history: lifecycle `replacement_id` continues to describe a deprecated
row's canonical successor, while `curriculum_catalog_aliases` records external
wording confirmed by a human.

The table uses four nullable, foreign-keyed target columns and an exactly-one
check rather than an unenforced polymorphic UUID. Partial unique indexes encode
the semantic identities: subject aliases are global; topic aliases include
subject and grade; subtopic aliases include topic; skill aliases include
subtopic. A repeated identical mapping is a no-op. A different target in the
same scope is retained as a controlled conflict in the task-created audit data.

Aliases are learned in the Image Solving promotion transaction, after the task
and methodology have been flushed but before the transaction commits. The
backend accepts a confirmation only when the source text came from extraction,
the target is the final reviewed selection, and that target was present in the
persisted recommendation's candidate list. Thus a displayed candidate, a
temporary click, a forged arbitrary selection, or a failed promotion cannot
teach an alias.

Resolution is exact, then scoped alias, then non-binding lexical candidates,
then new. Alias targets may be active or provisional. The loader follows a
deprecated target's replacement chain; rejected/deprecated targets without a
live replacement are omitted. Effective aliases are part of the sorted catalog
snapshot, so creating or changing one changes the recommendation fingerprint
without any provider call.
