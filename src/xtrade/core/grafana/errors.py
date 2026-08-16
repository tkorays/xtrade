"""Exception types raised by the Grafana SDK."""

from __future__ import annotations


class GrafanaError(Exception):
    """Base class for every error raised by :mod:`xtrade.core.grafana`."""


class GrafanaAPIError(GrafanaError):
    """Raised when the Grafana HTTP API returns a non-2xx response."""

    def __init__(self, status_code: int, url: str, body: object) -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"Grafana API error {status_code} for {url}: {body!r}")


class GrafanaAuthError(GrafanaError):
    """Raised at client construction when no usable credentials are configured."""
