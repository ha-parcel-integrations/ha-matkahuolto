"""Tests for the Matkahuolto API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.matkahuolto.api import (
    MatkahuoltoApiClient,
    MatkahuoltoApiError,
)
from custom_components.matkahuolto.const import TRACKING_API_URL

from .payloads import NOT_FOUND_SAMPLE, TEST_CODE, registered_sample

CODE = TEST_CODE


def _session_returning(status: int, body: object = None, headers: dict | None = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_parcel_on_success():
    sample = registered_sample(CODE)
    session = _session_returning(200, sample)
    client = MatkahuoltoApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["parcelNumber"] == CODE
    # the endpoint and the tracking code end up in the request
    call = session.get.call_args
    assert call[0][0] == TRACKING_API_URL
    assert call.kwargs["params"] == {"language": "en", "parcelNumber": CODE}


async def test_get_parcel_returns_none_when_not_found():
    """An unknown or not-yet-scanned code is a normal state, not an error."""
    client = MatkahuoltoApiClient(_session_returning(200, NOT_FOUND_SAMPLE))
    assert await client.async_get_parcel("BOGUS") is None


async def test_get_parcel_raises_on_error_status():
    client = MatkahuoltoApiClient(_session_returning(500, {}))
    with pytest.raises(MatkahuoltoApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_429_with_retry_after():
    client = MatkahuoltoApiClient(_session_returning(429, {}, {"Retry-After": "30"}))
    with pytest.raises(MatkahuoltoApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 30.0


async def test_get_parcel_raises_on_429_with_http_date_retry_after():
    """An HTTP-date Retry-After is not parsed as seconds; the caller backs off itself."""
    client = MatkahuoltoApiClient(
        _session_returning(429, {}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    )
    with pytest.raises(MatkahuoltoApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_get_parcel_raises_on_unparseable_body():
    client = MatkahuoltoApiClient(_session_returning(200, "not json"))
    with pytest.raises(MatkahuoltoApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = MatkahuoltoApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(MatkahuoltoApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unrecognised_shape():
    """Neither the notFound shape nor a populated one — do not guess, stop."""
    client = MatkahuoltoApiClient(_session_returning(200, {"unexpected": True}))
    with pytest.raises(MatkahuoltoApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = MatkahuoltoApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
