"""Register every persistence model on the shared SQLAlchemy metadata."""


def register_all_models() -> None:
    """Import all model families so their tables and mappers are registered.

    Imports are intentionally local: callers opt in to persistence registration,
    and Python's import cache makes repeated registration calls idempotent.
    """
    from app.infrastructure import (
        assessment_models,  # noqa: F401
        auth_models,  # noqa: F401
        authoring_models,  # noqa: F401
        checking_models,  # noqa: F401
        classroom_models,  # noqa: F401
        image_solving_models,  # noqa: F401
        paper_checking_review_models,  # noqa: F401
        remediation_models,  # noqa: F401
        scan_checking_models,  # noqa: F401
    )
