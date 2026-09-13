"""Diagnostics support for the Matkahuolto parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MatkahuoltoConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# ``raw`` (in ``incoming``/``delivered`` below) carries the API response
# completely untouched — that's fine for the user's own instance, but every
# field that can identify a person, an address or a specific parcel must be
# caught here before a diagnostics dump is shared publicly. ``description``
# and ``place`` routinely embed a pickup point's name and street address;
# ``senderReference``/``officeCode``/``latitude``/``longitude`` are
# per-event reference/coordinate fields with no reason to leave the
# integration unredacted.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    "pickup_point",
    # Matkahuolto payload fields
    "parcelNumber",
    "departureStation",
    "storedUntil",
    "place",
    "description",
    "senderReference",
    "officeCode",
    "latitude",
    "longitude",
    "shipmentId",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MatkahuoltoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Matkahuolto config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
