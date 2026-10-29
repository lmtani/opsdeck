"""Errors the use cases raise. The presentation layer maps them to HTTP.

Two families: DomainError is a rule of the application (something was not
found, or an input broke an invariant); ExternalServiceError is a port
whose backend failed (the Cloud Logging API, GitHub, the host
shell). The first is the caller's problem, the second is not.
"""


class DomainError(Exception):
    """Base class for expected, user-facing errors about the request itself."""


class NotFoundError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class ExternalServiceError(Exception):
    """A port's backend is down, rejected us or returned garbage (HTTP 502)."""


class LogProviderError(ExternalServiceError):
    """The log backend (the Cloud Logging API) failed."""


class CommandError(ExternalServiceError):
    """Launching a local command shortcut failed."""


class GithubError(ExternalServiceError):
    """The GitHub API failed, rejected the token or is rate limited."""


class GithubNotFoundError(GithubError):
    """The org, repo or endpoint does not exist (or the token cannot see it)."""
