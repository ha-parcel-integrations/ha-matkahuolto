"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That keeps the carrier-specific
mapping apart from the coordinator, and makes it trivially unit-testable.

Matkahuolto's tracking endpoint has no closed status enum — it hands back a
free English sentence per event (``description``) and expects the caller to
read it. Two consequences drive most of this file:

* Statuses are matched on stable **substrings** of that sentence, not an
  exact/closed set — see :data:`_STATUS_PATTERNS`.
* Some of those sentences embed a named pickup point (a shop and its street
  address) — location-identifying text a canonical field must not repeat
  verbatim. ``raw_status`` therefore never carries the carrier's own
  sentence; it carries a short, generic label per matched category instead
  (see :data:`_STATUS_PATTERNS`' third column). The full, unedited sentence
  stays available under ``raw`` for anyone who needs it directly from their
  own Home Assistant instance — it is simply never promoted to a
  suite-wide, aggregator-visible field.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    EVENT_TIMEZONE,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

_HELSINKI_TZ = ZoneInfo(EVENT_TIMEZONE)

# Where users report a description we do not map yet.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-matkahuolto/issues/new"
    "?template=unrecognised_status.yml"
)

# Carrier description (matched as a case-insensitive substring) -> canonical
# status, generic label.
#
# Order matters: checked top to bottom, first match wins. The terminal
# statuses (returned, delivered) are checked first so a coincidental overlap
# with an in-transit/registered phrase never wins by accident.
#
# Every row is confirmed either on the live-captured, redacted fixture in
# tests/payloads.py (2026-09-13) or as an exact quoted sentence in the
# research doc (the return-to-sender wording, seen on three
# recipient-authorised parcels the same day, discarded after inspection).
# Per the build plan, "only implement mappings present in the redacted
# fixture" — nothing below is speculative.
#
# 2026-09-13: four sentences unmapped in production on real, recipient-
# authorised parcels (drop-off-point and pickup-reminder/redirect wording
# never seen in the original fixture capture) — added below with the same
# confirmed-only bar, folded back into the research doc.
_STATUS_PATTERNS: list[tuple[str, ParcelStatus, str]] = [
    ("returned it to the sender", ParcelStatus.RETURNING, "Returned to sender"),
    ("the consignment has been delivered", ParcelStatus.DELIVERED, "Delivered"),
    (
        "available for pickup",
        ParcelStatus.AT_PICKUP_POINT,
        "Available for pickup at a pickup point",
    ),
    (
        "sent the recipient a message about the arrival",
        ParcelStatus.AT_PICKUP_POINT,
        "Arrival notification sent",
    ),
    (
        "forget to pick up your parcel",
        ParcelStatus.AT_PICKUP_POINT,
        "Available for pickup at a pickup point",
    ),
    (
        "pick up point has changed",
        ParcelStatus.IN_TRANSIT,
        "Redirected to a new pickup point",
    ),
    (
        "collected the consignment from the parcel pick-up point",
        ParcelStatus.IN_TRANSIT,
        "Collected from a drop-off point",
    ),
    (
        "deliver the consignment to the pickup point",
        ParcelStatus.IN_TRANSIT,
        "On its way to a pickup point",
    ),
    (
        "ready to continue its journey",
        ParcelStatus.IN_TRANSIT,
        "Ready to continue its journey",
    ),
    ("is on its way", ParcelStatus.IN_TRANSIT, "On its way"),
    ("has been sorted", ParcelStatus.IN_TRANSIT, "Sorted for onward transport"),
    (
        "waiting for the consignment from the sender",
        ParcelStatus.REGISTERED,
        "Awaiting handover from sender",
    ),
    (
        "ready for delivery at the parcel pick-up point",
        ParcelStatus.REGISTERED,
        "Ready for carrier pickup at a drop-off point",
    ),
    (
        "consignment is being processed",
        ParcelStatus.REGISTERED,
        "Being processed",
    ),
]

# Descriptions we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll. Keyed on the description
# text itself so a warning fires once per distinct new sentence — but the
# text is never included in the log line (it can carry a pickup point's name
# and address; CONVENTIONS.md's pre-1.0 rule is keys/structure, not values,
# for anything that could carry PII). Report a new sentence by following the
# link in the log and describing what you saw.
_unmapped_descriptions_logged: set[str] = set()


def _warn_unmapped_description(description: str) -> None:
    """Log an unmapped Matkahuolto description once, without its text."""
    if description in _unmapped_descriptions_logged:
        return
    _unmapped_descriptions_logged.add(description)
    _LOGGER.warning(
        "Unrecognised Matkahuolto tracking description (length=%d) — help us "
        "map it. Open an issue and describe what the parcel's status page "
        "showed: %s",
        len(description),
        NEW_ISSUE_URL,
    )


def _match_status(description: str | None) -> tuple[ParcelStatus, str | None]:
    """Match a carrier description to a canonical status and generic label.

    ``None``/empty (no event yet) reports ``unknown`` silently; an
    unrecognised sentence reports ``unknown`` with a one-shot warning. Both
    cases carry ``raw_status=None`` — the point of the generic-label table is
    that nothing outside it is ever echoed back verbatim.
    """
    if not description:
        return ParcelStatus.UNKNOWN, None
    lowered = description.lower()
    for needle, status, label in _STATUS_PATTERNS:
        if needle in lowered:
            return status, label
    _warn_unmapped_description(description)
    return ParcelStatus.UNKNOWN, None


def map_parcel_status(description: str | None) -> ParcelStatus:
    """Map a Matkahuolto event description to a canonical :class:`ParcelStatus`."""
    status, _label = _match_status(description)
    return status


def _parse_event_datetime(date_str: str | None, time_str: str | None) -> datetime | None:
    """Combine Matkahuolto's ``date``/``time`` fields into an aware datetime.

    ``date`` is ``DD.MM.YYYY`` and ``time`` is ``HH:MM``, both local Finnish
    wall-clock with no offset or zone marker (confirmed across three
    recipient-authorised parcels on 2026-09-13) — localise to
    ``Europe/Helsinki`` explicitly, never treat as UTC.
    """
    if not date_str or not time_str:
        return None
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=_HELSINKI_TZ)


def _to_iso_timestamp(date_str: str | None, time_str: str | None) -> str | None:
    """Return an ISO 8601 string for one event's ``date``/``time`` pair."""
    parsed = _parse_event_datetime(date_str, time_str)
    return parsed.isoformat() if parsed is not None else None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _generic_place(place: str | None) -> str | None:
    """Return a pickup point's name without its precise street address.

    Matkahuolto's ``place`` field on a pickup-point event is
    ``"<shop name>, <street address>"`` (e.g. ``"Example Pickup Point,
    Example Street 1"``). The shop name is the whole point of surfacing a
    pickup point; the street address is exactly the "precise location" the
    build plan says must not land in a user-visible attribute. Keep the part
    before the first comma only.
    """
    if not place:
        return None
    return place.split(",", 1)[0].strip() or None


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from Matkahuolto's ``trackingEvents``.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the matched category's
    generic label, never the carrier's own free-text sentence (see the module
    docstring). The API returns the array **newest-first** (confirmed across
    three recipient-authorised parcels on 2026-09-13); sort it back to
    oldest-first here regardless of the order fed in, so a caller doesn't have
    to remember to reverse first.
    """
    parseable: list[tuple[datetime, dict]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = _to_iso_timestamp(event.get("date"), event.get("time"))
        if not timestamp:
            continue
        _status, label = _match_status(event.get("description"))
        entry = {
            "timestamp": timestamp,
            "status": _status if _status is not ParcelStatus.UNKNOWN else None,
            "raw_status": label,
        }
        parseable.append((parse_iso(timestamp), entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable]
    return ordered[-max_events:]


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    Matkahuolto has no closed status enum, no weight/dimensions field and no
    delivery-window/ETA field in its anonymous payload — those keys are
    always ``None`` (see ``const.py``'s ``CAPABILITIES``). The current status
    comes from the newest tracking event (index 0 of ``trackingEvents``,
    since the array is newest-first); a delivered parcel has ``delivered_at``
    set from that same event and no ETA to clear.

    ``senderReference`` and each event's ``officeCode``/``latitude``/
    ``longitude`` are stripped out of ``raw`` before it is returned — the
    build plan calls these out by name as fields that must stay out of
    user-visible attributes, not just diagnostics (unlike ``place``, which is
    genericised for ``pickup_point`` but left intact in ``raw`` since it is a
    public pickup-point address, not personal data).
    """
    events = raw.get("trackingEvents") or []
    latest = events[0] if isinstance(events, list) and events else {}
    status, _label = _match_status(latest.get("description") if isinstance(latest, dict) else None)
    delivered = status is ParcelStatus.DELIVERED

    delivered_at = None
    if delivered and isinstance(latest, dict):
        delivered_at = _to_iso_timestamp(latest.get("date"), latest.get("time"))

    pickup_point = None
    if status is ParcelStatus.AT_PICKUP_POINT and isinstance(latest, dict):
        pickup_point = _generic_place(latest.get("place"))

    return {
        "carrier": "Matkahuolto",
        "barcode": raw.get("parcelNumber"),
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _label,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": pickup_point,
        "url": None,
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": _sanitize_raw(raw),
    }


def _sanitize_raw(raw: dict) -> dict:
    """Return a copy of the raw payload with reference/coordinate fields dropped.

    ``senderReference`` (top level) and ``officeCode``/``latitude``/
    ``longitude`` (per event) are the fields the build plan singles out as
    ones that must not reach a user-visible attribute at all, not just
    diagnostics — see the ``normalize_parcel`` docstring.
    """
    sanitized = dict(raw)
    sanitized.pop("senderReference", None)
    events = sanitized.get("trackingEvents")
    if isinstance(events, list):
        sanitized["trackingEvents"] = [
            {k: v for k, v in event.items() if k not in ("officeCode", "latitude", "longitude")}
            if isinstance(event, dict)
            else event
            for event in events
        ]
    return sanitized


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
