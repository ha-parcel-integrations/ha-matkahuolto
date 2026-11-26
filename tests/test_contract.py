"""The parcel contract's checks, run over every normalised sample."""

import copy
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.matkahuolto.const import ParcelStatus
from custom_components.matkahuolto.parcels import (
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import (
    DELIVERED_SAMPLE,
    _event,
    at_pickup_point_sample,
    in_transit_sample,
    registered_sample,
    returning_sample,
    unknown_status_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

HELSINKI = ZoneInfo("Europe/Helsinki")

SAMPLES = {
    "delivered": lambda: copy.deepcopy(DELIVERED_SAMPLE),
    "registered": registered_sample,
    "in_transit": in_transit_sample,
    "at_pickup_point": at_pickup_point_sample,
    "returning": returning_sample,
    "unknown": unknown_status_sample,
    "bare": lambda: {"parcelNumber": "EXAMPLE000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(copy.deepcopy(DELIVERED_SAMPLE))
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Pickup Point"
    assert (
        parcel["delivered_at"]
        == datetime(2026, 1, 21, 14, 41, tzinfo=HELSINKI).isoformat()
    )


def test_a_returned_parcel_keeps_its_pickup_point():
    parcel = normalize_parcel(returning_sample())
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Pickup Point"


def test_the_arrival_message_does_not_name_the_point():
    sample = at_pickup_point_sample()
    sample["trackingEvents"].insert(
        0,
        _event(
            "21.01.2026",
            "12:00",
            "We have sent the recipient a message about the arrival of the "
            "consignment (e-mail).",
            place="Example Town",
        ),
    )
    parcel = normalize_parcel(sample)
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Pickup Point"


def test_pickup_without_a_named_point_has_none():
    sample = at_pickup_point_sample()
    sample["trackingEvents"][0]["officeCode"] = None
    parcel = normalize_parcel(sample)
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_planned_window_shapes_in_the_carrier_zone():
    moment = planned_window("2026-04-29T13:00:00Z", None, zone=HELSINKI)
    end = datetime.fromisoformat(moment[1])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(moment[0]).astimezone(HELSINKI).date()

    start, end = (
        datetime.fromisoformat(value)
        for value in planned_window("2026-04-29", None, zone=HELSINKI)
    )
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
    assert start.utcoffset() == datetime(2026, 4, 29, tzinfo=HELSINKI).utcoffset()


def test_planned_window_is_emptied_for_finished_statuses():
    window = ("2099-01-01T10:00:00+02:00", "2099-01-01T23:59:59+02:00")
    for status in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert planned_for_status(status, *window) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window


def test_utc_is_not_assumed_for_naive_times():
    start, _ = planned_window(datetime(2099, 1, 1, 10, 0), None, zone=HELSINKI)
    assert parse_iso(start) == datetime(2099, 1, 1, 10, 0, tzinfo=HELSINKI)
    assert parse_iso(start) != datetime(2099, 1, 1, 10, 0, tzinfo=UTC)
