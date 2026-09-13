"""Sample Matkahuolto API payloads shared by the test modules.

``DELIVERED_SAMPLE`` is a **redacted, real captured response** — one paced,
recipient-authorised ``GET /search/trackingInfo`` (2026-09-13). The tracking
code, sender reference, precise pickup-point address and GPS coordinates
have all been replaced with generic placeholders; every status description,
field name, event ordering (newest-first) and date/time format is otherwise
unedited.

The other samples are synthetic variants built from the same confirmed field
shapes and status vocabulary, covering the
`registered`/`in_transit`/`at_pickup_point`/`returning` categories the single
captured parcel (a `delivered` one) does not itself exercise.
"""
from __future__ import annotations

TEST_CODE = "TEST_CODE"

NOT_FOUND_SAMPLE: dict = {"notFound": True}


def _event(date: str, time: str, description: str, **extra) -> dict:
    """One entry of Matkahuolto's own ``trackingEvents`` array."""
    return {
        "date": date,
        "time": time,
        "description": description,
        "officeCode": None,
        "place": None,
        "latitude": None,
        "longitude": None,
        "shipmentId": 1700000000000,
        **extra,
    }


# Redacted, real captured response (delivered parcel) — newest-first, exactly
# as the endpoint returned it, field for field.
DELIVERED_SAMPLE: dict = {
    "parcelNumber": TEST_CODE,
    "senderReference": "TEST-SENDER-REFERENCE",
    "departureStation": "Helsinki Logy",
    "productCategory": "Pick-up Parcel",
    "storedUntil": "2026-01-28",
    "trackingEvents": [
        _event(
            "21.01.2026",
            "14:41",
            "The consignment has been delivered. Thank you for choosing Matkahuolto!",
            place="Example Town",
        ),
        _event(
            "21.01.2026",
            "11:27",
            "We have sent the recipient a message about the arrival of the "
            "consignment (e-mail).",
            place="Example Town",
        ),
        _event(
            "21.01.2026",
            "11:27",
            "The shipment is available for pickup with the pickup code at "
            "the pick up point Example Pickup Point. Last day for pickup: "
            "28.1.2026.",
            officeCode="9129",
            place="Example Pickup Point",
        ),
        _event(
            "21.01.2026",
            "06:27",
            "We will deliver the consignment to the pickup point during the "
            "day. You will receive a notification as soon as it is ready "
            "for collection.",
            place="Example Town 00000",
        ),
        _event(
            "21.01.2026",
            "03:24",
            "Your parcel has been sorted and will be taken next to the pickup point.",
            place="Example Town 00000",
        ),
        _event(
            "21.01.2026",
            "03:24",
            "Consignment is being processed",
            place="Example Town 00000",
        ),
        _event(
            "20.01.2026",
            "18:08",
            "The consignment is on its way",
            place="Example City 00000",
        ),
        _event(
            "20.01.2026",
            "17:21",
            "The consignment is ready to continue its journey from Matkahuolto Example City",
            place="Example City 00000",
        ),
        _event(
            "20.01.2026",
            "01:16",
            "Consignment is being processed",
            place="Example City 00000",
        ),
        _event(
            "20.01.2026",
            "01:16",
            "Consignment is being processed",
            place="Example City",
        ),
        _event(
            "13.01.2026",
            "15:48",
            "Matkahuolto is waiting for the consignment from the sender. The "
            "consignment usually arrives at Matkahuolto within a couple of "
            "days. It takes longer for consignments arriving from abroad. "
            "The tracking information will be updated as soon as we "
            "receive the consignment.",
            place=None,
        ),
    ],
    "additionalServices": [],
    "shipmentId": 1700000000000,
    "returnShipment": False,
}


def registered_sample(code: str = TEST_CODE) -> dict:
    """A parcel Matkahuolto has not yet received from the sender."""
    return {
        "parcelNumber": code,
        "departureStation": None,
        "productCategory": "Pick-up Parcel",
        "storedUntil": None,
        "trackingEvents": [
            _event(
                "13.01.2026",
                "15:48",
                "Matkahuolto is waiting for the consignment from the sender. "
                "The consignment usually arrives at Matkahuolto within a "
                "couple of days.",
            ),
        ],
        "additionalServices": [],
        "shipmentId": 1700000000001,
        "returnShipment": False,
    }


def in_transit_sample(code: str = TEST_CODE) -> dict:
    """A parcel moving through the network."""
    return {
        "parcelNumber": code,
        "departureStation": "Example City",
        "productCategory": "Pick-up Parcel",
        "storedUntil": None,
        "trackingEvents": [
            _event("20.01.2026", "18:08", "The consignment is on its way"),
            _event(
                "20.01.2026",
                "01:16",
                "Consignment is being processed",
            ),
        ],
        "additionalServices": [],
        "shipmentId": 1700000000002,
        "returnShipment": False,
    }


def at_pickup_point_sample(code: str = TEST_CODE) -> dict:
    """A parcel ready for collection at a pickup point."""
    return {
        "parcelNumber": code,
        "departureStation": "Example City",
        "productCategory": "Pick-up Parcel",
        "storedUntil": "2026-01-28",
        "trackingEvents": [
            _event(
                "21.01.2026",
                "11:27",
                "The shipment is available for pickup with the pickup code "
                "at the pick up point Example Pickup Point.",
                officeCode="9129",
                place="Example Pickup Point, Example Street 1",
            ),
            _event("20.01.2026", "18:08", "The consignment is on its way"),
        ],
        "additionalServices": [],
        "shipmentId": 1700000000003,
        "returnShipment": False,
    }


def returning_sample(code: str = TEST_CODE) -> dict:
    """A parcel not picked up in time, going back to the sender."""
    return {
        "parcelNumber": code,
        "departureStation": "Example City",
        "productCategory": "Pick-up Parcel",
        "storedUntil": "2026-01-28",
        "trackingEvents": [
            _event(
                "29.01.2026",
                "09:00",
                "As the consignment was not picked up on time, we returned "
                "it to the sender.",
            ),
            _event(
                "21.01.2026",
                "11:27",
                "The shipment is available for pickup with the pickup code "
                "at the pick up point Example Pickup Point.",
                officeCode="9129",
                place="Example Pickup Point, Example Street 1",
            ),
        ],
        "additionalServices": [],
        "shipmentId": 1700000000004,
        "returnShipment": False,
    }


def unknown_status_sample(code: str = TEST_CODE) -> dict:
    """A parcel whose latest event description matches no known category."""
    return {
        "parcelNumber": code,
        "departureStation": "Example City",
        "productCategory": "Pick-up Parcel",
        "storedUntil": None,
        "trackingEvents": [
            _event(
                "01.02.2026",
                "09:00",
                "Something Matkahuolto has never described before happened.",
            ),
        ],
        "additionalServices": [],
        "shipmentId": 1700000000005,
        "returnShipment": False,
    }
