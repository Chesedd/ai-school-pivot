"""Reusable Origin validation for unsafe cookie-authenticated requests."""

from fastapi import HTTPException, Request

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class TrustedOriginPolicy:
    """Reject explicit foreign Origins; allow absent Origin for non-browser clients."""

    def __init__(self, trusted_origins: tuple[str, ...]):
        self.trusted_origins = frozenset(origin.rstrip("/") for origin in trusted_origins)

    def enforce(self, request: Request) -> None:
        if request.method.upper() in SAFE_METHODS:
            return
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in self.trusted_origins:
            raise HTTPException(status_code=403, detail="foreign_origin")
