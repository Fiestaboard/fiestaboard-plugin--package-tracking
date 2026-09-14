# Package Tracking Plugin

Track shipments from any carrier on your board using the [17TRACK](https://api.17track.net/) API.

**→ [Setup Guide](./docs/SETUP.md)** - Configuration instructions

## Overview

17TRACK is a carrier-agnostic tracking aggregator: one API key covers UPS, FedEx, USPS, DHL,
Amazon Logistics, China Post and ~2000 more carriers, with no per-carrier setup. The plugin
registers each of your tracking numbers with 17TRACK once, then polls `gettrackinfo` on every
refresh and exposes each package's carrier, normalized status and latest scan event.

## Template Variables

```
{{package_tracking.count}}                     # Number of tracked packages
{{package_tracking.delivered_count}}           # How many are Delivered
{{package_tracking.in_transit_count}}          # How many are In Transit
{{package_tracking.out_for_delivery_count}}    # How many are Out for Delivery
{{package_tracking.next_label}}                # Label of the most urgent package
{{package_tracking.next_status}}               # Status of the most urgent package

{{package_tracking.packages.0.label}}           # "Mom's gift" (or the number if unlabeled)
{{package_tracking.packages.0.number}}          # 1Z999AA10123456784
{{package_tracking.packages.0.carrier}}         # UPS
{{package_tracking.packages.0.status}}          # In Transit
{{package_tracking.packages.0.status_code}}     # InTransit (raw 17TRACK status)
{{package_tracking.packages.0.last_event}}      # Arrived at facility
{{package_tracking.packages.0.last_update}}     # Sep 13 4:12PM
{{package_tracking.packages.0.days_in_transit}} # 4
```

`packages` is an array; index it `0`-`9` in the order the tracking numbers were entered.

"Most urgent" ranks statuses: Out for Delivery > Ready for Pickup > Delivery Failed > Exception >
In Transit > Pending > Expired > Delivered.

### Status values

Raw 17TRACK codes are normalized into display strings:

| `status_code` | `status` |
|---------------|----------|
| `NotFound`, `InfoReceived` | Pending |
| `InTransit` | In Transit |
| `OutForDelivery` | Out for Delivery |
| `AvailableForPickup` | Ready for Pickup |
| `DeliveryFailure` | Delivery Failed |
| `Delivered` | Delivered |
| `Exception` | Exception |
| `Expired` | Expired |

Any status 17TRACK adds later passes through unchanged.

## Example Templates

### Package list

```
PACKAGES
{{package_tracking.packages.0.label}} {{package_tracking.packages.0.status}}
{{package_tracking.packages.1.label}} {{package_tracking.packages.1.status}}
{{package_tracking.packages.2.label}} {{package_tracking.packages.2.status}}
```

### Headline

```
{{package_tracking.next_label}}
{{package_tracking.next_status}}
{{package_tracking.packages.0.last_event}}
```

`next_status` has default color rules, so the board shows a colored tile before the value:
green for Delivered, yellow for Out for Delivery, red for Exception, blue for In Transit.

## Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| enabled | boolean | false | Enable/disable the plugin |
| api_key | password | — | 17TRACK security key (required) |
| tracking_numbers | string | — | Up to 10, comma- or newline-separated, optional `label:number` |
| auto_register | boolean | true | Register new numbers with 17TRACK automatically |
| refresh_seconds | integer | 1800 | How often to poll (min 600) |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PACKAGE_TRACKING_ENABLED` | `false` | Enable the plugin |
| `PACKAGE_TRACKING_API_KEY` | — | 17TRACK key, as an alternative to the UI setting |

## API

[17TRACK API v2.2](https://api.17track.net/en/doc). A free account includes roughly 100 tracking
number registrations per month; polling a number that is already registered is free. Registration
is one-time per number, so the quota limits how many *new* packages you track per month, not how
often the board refreshes.

Endpoints used:

- `POST https://api.17track.net/track/v2.2/register` — one-time per tracking number
- `POST https://api.17track.net/track/v2.2/gettrackinfo` — every refresh

Both take a JSON array of `{"number": "..."}` and authenticate with a `17token` header.
The "already registered" rejection (`-18019901`) is treated as success.

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests mock all network calls; no API key is needed to run them.

## Author

FiestaBoard Team
