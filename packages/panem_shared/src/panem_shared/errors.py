"""Service-layer exceptions. Cogs catch these and map them to `strings.py`
replies; services never format a user-facing string themselves (NFR-10).

Lives in `panem_shared` (re-exported from `panem_bot.errors` for every
existing `from panem_bot.errors import NotAllowed`-style call site) rather
than only `panem_bot`, the same reasoning as `panem_shared.jail`/
`panem_shared.stealing`/`panem_shared.characters`: a service function that
raises one of these has to be callable from `panem_api`'s dashboard
endpoints too, and `panem_api` cannot import `panem_bot`."""

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
