"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matkahuolto.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.matkahuolto.parcels import (
    apply_delivered_filter,
    build_history,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
)

from .payloads import (
    DELIVERED_SAMPLE,
    at_pickup_point_sample,
    in_transit_sample,
    registered_sample,
    returning_sample,
    unknown_status_sample,
)

# ---------------------------------------------------------------------------
# map_parcel_status — substring matching on free-text descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description,expected",
    [
        (
            "Matkahuolto is waiting for the consignment from the sender.",
            ParcelStatus.REGISTERED,
        ),
        ("Consignment is being processed", ParcelStatus.REGISTERED),
        ("The consignment is on its way", ParcelStatus.IN_TRANSIT),
        (
            "The consignment is ready to continue its journey from Matkahuolto Vantaa",
            ParcelStatus.IN_TRANSIT,
        ),
        (
            "Your parcel has been sorted and will be taken next to the pickup point.",
            ParcelStatus.IN_TRANSIT,
        ),
        (
            "We will deliver the consignment to the pickup point during the day.",
            ParcelStatus.IN_TRANSIT,
        ),
        (
            "The shipment is available for pickup with the pickup code at the "
            "pick up point Example Pickup Point.",
            ParcelStatus.AT_PICKUP_POINT,
        ),
        (
            "We have sent the recipient a message about the arrival of the "
            "consignment (e-mail).",
            ParcelStatus.AT_PICKUP_POINT,
        ),
        (
            "The consignment has been delivered. Thank you for choosing Matkahuolto!",
            ParcelStatus.DELIVERED,
        ),
        (
            "As the consignment was not picked up on time, we returned it to the sender.",
            ParcelStatus.RETURNING,
        ),
        (
            "Don’t forget to pick up your parcel. Last pick up date 11.11.2025.",
            ParcelStatus.AT_PICKUP_POINT,
        ),
        (
            "Your pick up point has changed (reason). We will deliver the "
            "package to the nearest available pick up point.",
            ParcelStatus.IN_TRANSIT,
        ),
        (
            "We have collected the consignment from the Parcel Pick-up Point "
            "Example Pickup Point.",
            ParcelStatus.IN_TRANSIT,
        ),
        (
            "The consignment is ready for delivery at the Parcel Pick-up Point "
            "Example Pickup Point. It will be picked up on our driver's next call.",
            ParcelStatus.REGISTERED,
        ),
    ],
)
def test_map_parcel_status_known(description, expected):
    assert map_parcel_status(description) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("Something never described before.") == ParcelStatus.UNKNOWN


def test_status_matching_is_case_insensitive():
    assert map_parcel_status("THE CONSIGNMENT HAS BEEN DELIVERED.") == ParcelStatus.DELIVERED


def test_returning_wins_over_delivered_band_ordering_trap():
    """"we returned it to the sender" must not be swallowed by a delivered match."""
    assert (
        map_parcel_status(
            "As the consignment was not picked up on time, we returned it to "
            "the sender."
        )
        == ParcelStatus.RETURNING
    )


def test_unmapped_status_warns_only_once_without_leaking_the_text(caplog):
    """The description text itself must never be logged — it can carry a
    pickup point's name and street address."""
    text = "A brand new description Matkahuolto has never used before."
    assert map_parcel_status(text) == ParcelStatus.UNKNOWN
    assert map_parcel_status(text) == ParcelStatus.UNKNOWN
    assert caplog.text.count("Unrecognised Matkahuolto") == 1
    assert text not in caplog.text
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_finnish_local_datetime_is_not_treated_as_utc():
    """DD.MM.YYYY + HH:MM carries no offset and must localise to Helsinki,
    never UTC — the exact trap the build plan calls out."""
    parcel = normalize_parcel(DELIVERED_SAMPLE)
    delivered_at = parse_iso(parcel["delivered_at"])
    assert delivered_at is not None
    # Helsinki is UTC+2 in January (EET, no DST) — a UTC timestamp would
    # carry a +00:00 offset instead.
    assert delivered_at.utcoffset() == timedelta(hours=2)
    assert delivered_at.astimezone(timezone.utc).hour == 12  # 14:41 EET -> 12:41 UTC
    assert delivered_at.astimezone(timezone.utc).minute == 41


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    """The API returns trackingEvents newest-first; history must be reversed."""
    history = build_history(DELIVERED_SAMPLE["trackingEvents"])
    assert len(history) == len(DELIVERED_SAMPLE["trackingEvents"])
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED
    # Strictly ascending timestamps.
    timestamps = [parse_iso(entry["timestamp"]) for entry in history]
    assert timestamps == sorted(timestamps)


def test_build_history_never_echoes_the_raw_description():
    """raw_status is always the generic label, never the carrier's own
    free-text sentence — which can embed a pickup point's name and address."""
    history = build_history(DELIVERED_SAMPLE["trackingEvents"])
    raw_texts = {event["description"] for event in DELIVERED_SAMPLE["trackingEvents"]}
    for entry in history:
        assert entry["raw_status"] not in raw_texts


def test_build_history_caps_to_max_events():
    events = [
        {"date": f"{day:02d}.01.2026", "time": "10:00", "description": "The consignment is on its way"}
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"description": "no timestamp fields"}]) == []
    assert build_history(["not-a-dict"]) == []


def test_build_history_unmapped_event_keeps_null_status():
    history = build_history(
        [{"date": "01.02.2026", "time": "09:00", "description": "Never seen before."}]
    )
    assert history[0]["status"] is None
    assert history[0]["raw_status"] is None


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(DELIVERED_SAMPLE)) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample."""
    delivered = normalize_parcel(DELIVERED_SAMPLE)
    pickup = normalize_parcel(at_pickup_point_sample())
    with_history = normalize_parcel(DELIVERED_SAMPLE, include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert delivered["planned_from"] is not None or delivered["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_never_populates_undeclared_capabilities():
    """Anything not in CAPABILITIES must come back as a literal None — never
    omitted, and never silently populated without updating the docs-site claim."""
    parcel = normalize_parcel(DELIVERED_SAMPLE, include_history=True)
    if "weight" not in CAPABILITIES:
        assert parcel["weight"] is None
    if "dimensions" not in CAPABILITIES:
        assert parcel["dimensions"] is None
    if "delivery_window" not in CAPABILITIES:
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None
    if "url" not in CAPABILITIES:
        assert parcel["url"] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(DELIVERED_SAMPLE)
    assert parcel["carrier"] == "Matkahuolto"
    assert parcel["barcode"] == "TEST_CODE"
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] is not None
    # A delivered parcel drops its ETA — the window is meaningless once it has
    # arrived (Matkahuolto's anonymous payload has no ETA field at all).
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(DELIVERED_SAMPLE, include_history=True)
    assert len(parcel["history"]) == len(DELIVERED_SAMPLE["trackingEvents"])
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_registered_parcel():
    parcel = normalize_parcel(registered_sample())
    assert parcel["status"] == ParcelStatus.REGISTERED
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_in_transit_parcel():
    parcel = normalize_parcel(in_transit_sample())
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is False


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(at_pickup_point_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    # The store name is kept, the street address after the comma is not.
    assert parcel["pickup_point"] == "Example Pickup Point"


def test_normalize_returning_parcel():
    parcel = normalize_parcel(returning_sample())
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["delivered"] is False


def test_normalize_unknown_status_parcel():
    parcel = normalize_parcel(unknown_status_sample())
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None
    assert parcel["delivered"] is False


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"parcelNumber": "PENDING000001"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_missing_events_key_does_not_crash():
    parcel = normalize_parcel({"parcelNumber": "NOEVENTS"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["pickup_point"] is None


def test_normalize_null_and_missing_event_fields_do_not_crash():
    raw = {
        "parcelNumber": "PARTIAL",
        "trackingEvents": [
            {"date": None, "time": None, "description": None, "place": None}
        ],
    }
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered_at"] is None


def test_normalize_keeps_the_original_payload_structure_under_raw():
    parcel = normalize_parcel(DELIVERED_SAMPLE)
    assert parcel["raw"]["parcelNumber"] == "TEST_CODE"
    assert len(parcel["raw"]["trackingEvents"]) == len(DELIVERED_SAMPLE["trackingEvents"])


def test_normalize_strips_reference_and_coordinate_fields_from_raw():
    """senderReference/officeCode/latitude/longitude must not reach a
    user-visible attribute at all — the build plan calls these out by name,
    stronger than the usual diagnostics-only redaction."""
    parcel = normalize_parcel(DELIVERED_SAMPLE)
    assert "senderReference" not in parcel["raw"]
    for event in parcel["raw"]["trackingEvents"]:
        assert "officeCode" not in event
        assert "latitude" not in event
        assert "longitude" not in event


def test_normalize_does_not_mutate_the_input():
    raw = at_pickup_point_sample()
    import copy

    original = copy.deepcopy(raw)
    normalize_parcel(raw)
    assert raw == original


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
