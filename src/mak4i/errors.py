"""Domain errors that carry a MAK-0008 §9 error code.

The engine raises these; transports translate them (the MCP server into
`structuredContent: {"error": {code, message, retryable}}`, the CLI into a
message). Messages never contain secrets and never reveal whether something
the caller can't read exists.
"""

from __future__ import annotations


class MAK4IError(Exception):
    code: str = "internal_error"
    retryable: bool = False

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationFailedError(MAK4IError, ValueError):
    code = "validation_error"


class NotFoundError(MAK4IError):
    code = "not_found"


class SubjectInConflictError(MAK4IError):
    """MAK-0004 §A3.7: a candidate of an open conflict may not release its
    subject key; the conflict must be resolved instead."""

    code = "subject_in_conflict"


class ConflictChangedError(MAK4IError):
    """MAK-0004 §A8.1/§A8.3: the conflict's candidates changed (or a
    resolution is in progress); re-read and retry."""

    code = "conflict_changed"
    retryable = True


class ConflictNotOpenError(MAK4IError):
    code = "conflict_not_open"


class SubjectCollisionError(MAK4IError):
    code = "subject_collision"


class IdentityMismatchError(MAK4IError):
    code = "identity_mismatch"
