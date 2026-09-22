# Matkahuolto Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-matkahuolto.svg)](https://github.com/ha-parcel-integrations/ha-matkahuolto/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-matkahuolto/total.svg)](https://github.com/ha-parcel-integrations/ha-matkahuolto/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Matkahuolto](https://www.matkahuolto.fi/seuranta) (Finland) parcels. No account is needed — you enter the tracking code yourself, just like on the Matkahuolto website.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Matkahuolto parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `at_pickup_point` / `delivered` / `returning` / …) and a generic label for the carrier's own status text
- Pickup-point name surfaced (without its precise street address) when a parcel is ready for collection
- Summary sensors: incoming parcels, recently delivered parcels, parcels awaiting pickup
- `matkahuolto.track_parcel` / `matkahuolto.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- A Matkahuolto parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-matkahuolto` as an **Integration**.
3. Install **Matkahuolto** and restart Home Assistant.

### Manual

Copy `custom_components/matkahuolto` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Matkahuolto**. There is nothing to fill in: the hub is created immediately (Matkahuolto tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`matkahuolto.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet — this is the fallback that fires in practice for
  Matkahuolto, whose anonymous tracking endpoint never returns an ETA or
  delivery window at all).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Matkahuolto hub
  out there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Matkahuolto → ⋮ → Delete**. Nothing is stored on Matkahuolto's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.matkahuolto_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.matkahuolto_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.matkahuolto_next_delivery` | Always empty for this carrier — see note below |
| `sensor.matkahuolto_awaiting_pickup` | Parcels ready for collection at a pickup point |
| `sensor.matkahuolto_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.matkahuolto_last_successful_update` | Diagnostic: when Matkahuolto was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

**No expected-delivery date.** Matkahuolto's anonymous tracking endpoint never returns an ETA or delivery window — only a pickup deadline once a parcel reaches a pickup point (surfaced in `raw`, not as a canonical field). `sensor.matkahuolto_next_delivery` and the **Deliveries** calendar therefore exist (every carrier in the suite ships them) but stay permanently empty on this integration; they are not a sign anything is broken.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. Matkahuolto's tracker has no closed status code list — it hands back a free English sentence per event, matched on stable substrings:

| Status | Meaning |
|---|---|
| `registered` | Matkahuolto is still waiting for the consignment from the sender, or it is being processed |
| `in_transit` | Moving through the network (sorted, on its way, or on its way to a pickup point) |
| `at_pickup_point` | Ready for collection, or an arrival notification was sent |
| `delivered` | Delivered |
| `returning` | Not picked up in time — going back to the sender |
| `unknown` | Not yet scanned, or a description Matkahuolto has not used before |

`out_for_delivery` and `problem` exist in the shared enum but have not been observed on this carrier. A short generic label is always available as `raw_status` — the carrier's own sentence is never republished verbatim, since it can embed a pickup point's name and street address.

## Events

The integration fires these on the event bus (also available as device triggers on the Matkahuolto device):

| Event | When |
|---|---|
| `matkahuolto_parcel_registered` | A new parcel appears in the active list |
| `matkahuolto_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `matkahuolto_parcel_delivered` | A parcel is delivered |

`matkahuolto_parcel_delivery_time_changed` exists in the shared event contract but never fires on this carrier — there is no ETA field to change (see the Sensors note above).

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `matkahuolto.track_parcel` | `tracking_code` | Start tracking a parcel |
| `matkahuolto.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.matkahuolto: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Matkahuolto has not scanned it yet (their API answers `{"notFound": true}` until the first scan), or the code is wrong. It will pick up automatically once scanned.
- **A log line says "Unrecognised Matkahuolto tracking description"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-matkahuolto/issues/new/choose) and describe, in your own words, what the parcel's status page showed at the time (the log deliberately never includes the carrier's own text, since it can carry a pickup point's name and address).

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Matkahuolto, Home Assistant, or any other third party referenced in this project. Please don't contact Matkahuolto for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Matkahuolto's terms. Data is sent only to Matkahuolto's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking endpoint as the Matkahuolto consumer website.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
