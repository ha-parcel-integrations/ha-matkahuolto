"""Tests for Matkahuolto diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.matkahuolto.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "EXAMPLE123456"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "EXAMPLE123456",
            "sender": None,
            "receiver": None,
            "status": "at_pickup_point",
            "pickup_point": "Example Pickup Point",
            "raw": {
                "parcelNumber": "EXAMPLE123456",
                "senderReference": None,
                "trackingEvents": [
                    {
                        "date": "21.01.2026",
                        "time": "11:27",
                        "description": "The shipment is available for pickup "
                        "with the pickup code at the pick up point Example "
                        "Pickup Point.",
                        "place": "Example Pickup Point, Example Street 1",
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes, the pickup point and every free-text/location field are
    # redacted, at every nesting level.
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["pickup_point"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["parcelNumber"] == "**REDACTED**"
    assert (
        result["incoming"][0]["raw"]["trackingEvents"][0]["description"]
        == "**REDACTED**"
    )
    assert result["incoming"][0]["raw"]["trackingEvents"][0]["place"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "at_pickup_point"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
