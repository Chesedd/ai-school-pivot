"""Bounded errors exposed by the authentication application boundary."""


class AuthenticationError(Exception):
    code = "authentication_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"


class InactiveAccountError(AuthenticationError):
    code = "inactive_account"


class InvalidSessionError(AuthenticationError):
    code = "invalid_session"


class ExpiredSessionError(AuthenticationError):
    code = "expired_session"


class SessionCreationError(AuthenticationError):
    code = "session_creation_failed"


class AccountAlreadyExistsError(AuthenticationError):
    code = "account_already_exists"


class InvalidAccountInputError(AuthenticationError):
    code = "invalid_account_input"
