"""Matkahuolto public tracking API client.

Anonymous, keyless ``GET /search/trackingInfo`` on the same host the official
web tracker calls. Confirmed live on a recipient-authorised parcel
(2026-09-13): populated responses carry ``trackingEvents``; an unknown or
not-yet-visible code answers ``{"notFound": true}`` with **HTTP 200**, not a
404 — that response shape is a normal "not found" state, never an error.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class MatkahuoltoApiError(Exception):
    """Raised when a Matkahuolto API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Matkahuolto API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class MatkahuoltoApiClient:
    """Client for the anonymous Matkahuolto tracking endpoint.

    No authentication and no rate-limit signal has ever been observed — a
    conservative, fixed polling cadence (the suite's usual dynamic tiers,
    see ``const.py``) is used rather than reacting to a header that doesn't
    exist yet.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the parcel dict for a known parcel, or ``None`` when the
        endpoint reports ``{"notFound": true}`` — also what a wrong, expired
        or not-yet-visible code gets. Any other failure — a non-2xx status, a
        non-JSON body (an HTML challenge or error page), or a JSON body that
        is not a dict — raises :class:`MatkahuoltoApiError` rather than being
        parsed as parcel data. Network errors propagate as
        ``aiohttp.ClientError``.
        """
        async with self._session.get(
            TRACKING_API_URL,
            params={"language": "en", "parcelNumber": tracking_code},
        ) as response:
            if response.status == 429:
                # Never observed live — no rate limit has been documented or
                # hit — but kept so a future one is handled by the suite's
                # standard backoff instead of falling through as a plain
                # fetch failure.
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; the caller's own backoff handles it
                raise MatkahuoltoApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise MatkahuoltoApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: guard against a body served as
                # text/plain rather than application/json — still parse it,
                # but never silently accept an HTML error/challenge page as
                # if it were parcel data.
                payload = await response.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError) as err:
                raise MatkahuoltoApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise MatkahuoltoApiError("unexpected body (not a JSON object)")

        if payload.get("notFound"):
            return None

        if "trackingEvents" not in payload and "parcelNumber" not in payload:
            # Neither the known "not found" shape nor a populated response —
            # a route change the build plan says to stop and re-check, not
            # to guess a shape for.
            raise MatkahuoltoApiError("unrecognised response shape")

        return payload
