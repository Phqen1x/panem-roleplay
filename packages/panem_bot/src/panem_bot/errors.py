"""Service-layer exceptions. Cogs catch these and map them to `strings.py`
replies; services never format a user-facing string themselves (NFR-10)."""

from __future__ import annotations


class ServiceError(Exception):
    """Base for all service-layer refusals. `reason_key` indexes `strings.py`."""

    def __init__(self, reason_key: str, **fmt: object) -> None:
        self.reason_key = reason_key
        self.fmt = fmt
        super().__init__(reason_key)


class ValidationFailed(ServiceError):
    """Input failed field validation (FR-CHR-2/7)."""


class NotAllowed(ServiceError):
    """The actor is not permitted to perform this action."""


class NotFound(ServiceError):
    """The referenced row does not exist."""


class LimitReached(ServiceError):
    """A configured cap (characters per user, scenes per district, ...) is hit."""
