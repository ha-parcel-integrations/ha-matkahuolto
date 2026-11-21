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
  address) — location-identifying text that has no business being echoed
  into a suite-wide, aggregator-visible field. ``raw_status`` therefore
  never carries the carrier's own sentence; it carries a short, generic
  label per matched category instead (see :data:`_STATUS_PATTERNS`' third
  column). The full, unedited sentence stays available under ``raw`` for
  anyone who needs it directly from their own Home Assistant instance — it
  is simply never promoted past that one field.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    EVENT_TIMEZONE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
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
# Two known future risks to revisit only once actually observed (do not
# pre-emptively reorder on a guess): a "delivered to pickup point X"-style
# sentence would match "the consignment has been delivered" and terminate as
# `delivered` ahead of the pickup-point rows below it; and "is on its way"
# is generic enough that a future "on its way back to the sender" wording
# could match `in_transit` before ever reaching `returning`.
#
# Every row is confirmed on a real, recipient-authorised parcel — either the
# redacted fixture in tests/payloads.py (2026-09-13) or a description seen
# live in production the same day. Nothing below is speculative or guessed
# from a code list; nothing is added without a live sighting first.
#
# 2026-09-13: four sentences unmapped in production on real, recipient-
# authorised parcels (drop-off-point and pickup-reminder/redirect wording
# never seen in the original fixture capture) — added below with the same
# confirmed-only bar.
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


def _parse_event_datetime(
    date_str: str | None, time_str: str | None
) -> datetime | None:
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _generic_place(place: str | None) -> str | None:
    """Return a pickup point's name without its precise street address.

    Matkahuolto's ``place`` field on a pickup-point event is
    ``"<shop name>, <street address>"`` (e.g. ``"Example Pickup Point,
    Example Street 1"``). This only trims what the **canonical**
    ``pickup_point`` field shows — the shop name is the whole point of
    surfacing a pickup point, the street address is more precision than that
    field needs. It does not hide anything: the full, untouched ``place``
    stays under ``raw`` (see ``normalize_parcel``), and only the address
    itself, not the fact that a pickup point exists, is trimmed here. Keep
    the part before the first comma only.
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
        status, label = _match_status(event.get("description"))
        entry = {
            "timestamp": timestamp,
            "status": status,
            "raw_status": label,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            continue
        parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable]
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    Matkahuolto has no closed status enum, no weight/dimensions field and no
    delivery-window/ETA field in its anonymous payload — those keys are
    always ``None`` (see ``const.py``'s ``CAPABILITIES``). The current status
    comes from the newest tracking event (index 0 of ``trackingEvents``,
    since the array is newest-first); a delivered parcel has ``delivered_at``
    set from that same event; ``pickup`` stays true once any event reached a
    pickup point, so it survives collection.

    ``raw`` is the API response completely untouched — including
    ``senderReference`` and each event's ``officeCode``/``latitude``/
    ``longitude``/``place``/``description``. It is the user's own data on
    their own instance; nothing is stripped from it. What must not leave the
    integration unredacted is ``diagnostics.py``'s public export
    (``TO_REDACT``), not this field.
    """
    events = raw.get("trackingEvents") or []
    latest = events[0] if isinstance(events, list) and events else {}
    status, _label = _match_status(
        latest.get("description") if isinstance(latest, dict) else None
    )
    delivered = status is ParcelStatus.DELIVERED

    delivered_at = None
    if delivered and isinstance(latest, dict):
        delivered_at = _to_iso_timestamp(latest.get("date"), latest.get("time"))

    event_dicts = [event for event in events if isinstance(event, dict)]
    event_statuses = [
        _match_status(event.get("description"))[0] for event in event_dicts
    ]
    pickup = is_pickup(None, status, event_statuses)

    pickup_point = None
    if pickup:
        for event, event_status in zip(event_dicts, event_statuses, strict=True):
            if event_status is ParcelStatus.AT_PICKUP_POINT and event.get("officeCode"):
                pickup_point = _generic_place(event.get("place"))
                break

    planned_from, planned_to = planned_for_status(status, None, None)

    barcode = raw.get("parcelNumber")

    return {
        "carrier": "Matkahuolto",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _label,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": pickup_point,
        "url": tracking_url(barcode),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
