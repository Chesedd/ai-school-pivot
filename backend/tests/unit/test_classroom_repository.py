"""Focused tests for PostgreSQL student provisioning conflict metadata."""

from sqlalchemy.exc import IntegrityError

from app.infrastructure.classroom_repository import _student_provisioning_conflict


class _DriverError(Exception):
    def __init__(self, constraint_name):
        super().__init__(constraint_name)
        self.constraint_name = constraint_name


class _AdapterError(Exception):
    pass


def _asyncpg_integrity_error(constraint_name):
    """Model SQLAlchemy's adapter error chained from asyncpg's driver error."""
    try:
        raise _DriverError(constraint_name)
    except _DriverError as driver_error:
        try:
            raise _AdapterError("translated by SQLAlchemy") from driver_error
        except _AdapterError as adapter_error:
            return IntegrityError("INSERT", {}, adapter_error)


def test_classifies_asyncpg_external_ref_constraint():
    error = _asyncpg_integrity_error("uq_students_group_external_ref")

    assert _student_provisioning_conflict(error) == "external_ref"


def test_accepts_constraint_metadata_exposed_on_adapter_error():
    adapter_error = _AdapterError("translated by SQLAlchemy")
    adapter_error.constraint_name = "uq_students_group_external_ref"

    assert _student_provisioning_conflict(
        IntegrityError("INSERT", {}, adapter_error)
    ) == "external_ref"


def test_classifies_both_student_link_unique_constraints():
    assert _student_provisioning_conflict(
        _asyncpg_integrity_error("pk_student_user_links")
    ) == "link"
    assert _student_provisioning_conflict(
        _asyncpg_integrity_error("uq_student_user_links_student_id")
    ) == "link"


def test_does_not_relabel_unknown_integrity_errors_as_link_conflicts():
    error = _asyncpg_integrity_error("fk_student_user_links_user_id_users")

    assert _student_provisioning_conflict(error) is None
