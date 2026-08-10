# Typed Methodology Foundation verification

Revision `20260810_02` is additive over `20260810_01`. Verify upgrade on PostgreSQL
with `alembic upgrade head`, then confirm `alembic current` and `alembic heads` both
report `20260810_02`. Existing accepted answers must report `legacy_untyped` and
retain every legacy field byte-for-byte.

The database rejects incompatible typed shapes, non-finite/negative tolerances,
unsafe unit codes, cross-version option membership, option duplicates, and empty
choice sets. Application validation additionally enforces answer-format/kind
compatibility, single-choice cardinality, allowlisted policy/version pairs, catalogue
references, and complete per-option rules. Version cloning copies catalogues,
accepted sets, typed fields, scoring policies, and rules while assigning new IDs.
No Checking route or checker is part of this revision.
