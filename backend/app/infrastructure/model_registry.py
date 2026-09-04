"""Register every persistence model on the shared SQLAlchemy metadata."""


def register_all_models() -> None:
    """Import all model families so their tables and mappers are registered.

    Imports are intentionally local: callers opt in to persistence registration,
    and Python's import cache makes repeated registration calls idempotent.
    """
    from app.infrastructure import assessment_models  # noqa: F401
    from app.infrastructure import auth_models  # noqa: F401
    from app.infrastructure import authoring_models  # noqa: F401
    from app.infrastructure import checking_models  # noqa: F401
    from app.infrastructure import image_solving_models  # noqa: F401
