# Package Tracking Setup

Show the status of your incoming packages on the board, using the free
[17TRACK](https://api.17track.net/) tracking API.

## Overview

**What it does:**
- Tracks up to 10 shipments at once, from any carrier
- Shows carrier, status, the latest scan event and how long each package has been travelling
- Highlights the most urgent package (out for delivery first, delivered last)

**Prerequisites:**
- ✅ A free 17TRACK API account (takes about two minutes)
- ✅ Internet connection (to reach api.17track.net)

## Quick Setup

### 1. Get a 17TRACK API Key

1. Go to [api.17track.net](https://api.17track.net/) and sign up for a free account
2. Open **Settings → Security Key**
3. Copy the key — it looks like a 32-character hex string

The free tier covers roughly **100 tracking number registrations per month**. Each number is
registered once; after that, checking it costs nothing. So the quota limits how many *new*
packages you can add each month, not how often the board refreshes.

### 2. Enable the Plugin

**Option A: Web UI**
1. Go to **Integrations** and find "Package Tracking"
2. Paste your key into **17TRACK API Key**
3. Enter your tracking numbers (see below)
4. Toggle **Enable Package Tracking** to on
5. Click **Save Changes**

**Option B: Environment Variables**

Add to your `.env` file:
```bash
PACKAGE_TRACKING_ENABLED=true
PACKAGE_TRACKING_API_KEY=your_17track_security_key
```

Tracking numbers are still entered in the web UI.

### 3. Enter Tracking Numbers

One per line, or comma-separated. Up to 10.

```
1Z999AA10123456784
9400111899223197428490
```

Add a friendly label with `label:number` — the label is what shows on the board:

```
Mom's gift:1Z999AA10123456784
Headphones:9400111899223197428490
Laptop, EE123456789US
```

Without a label the tracking number itself is used, which rarely fits on a board line — labels
are recommended.

### 4. Use in Templates

Summary variables:
- `{{package_tracking.count}}` — how many packages are tracked
- `{{package_tracking.delivered_count}}` — how many are delivered
- `{{package_tracking.in_transit_count}}` — how many are in transit
- `{{package_tracking.out_for_delivery_count}}` — how many are out for delivery
- `{{package_tracking.next_label}}` / `{{package_tracking.next_status}}` — the most urgent package

Per-package variables (index `0`-`9`, in the order you entered the numbers):
- `{{package_tracking.packages.0.label}}`
- `{{package_tracking.packages.0.number}}`
- `{{package_tracking.packages.0.carrier}}`
- `{{package_tracking.packages.0.status}}`
- `{{package_tracking.packages.0.status_code}}`
- `{{package_tracking.packages.0.last_event}}`
- `{{package_tracking.packages.0.last_update}}`
- `{{package_tracking.packages.0.days_in_transit}}`

### 5. Example Template

```
PACKAGES
{{package_tracking.packages.0.label}} {{package_tracking.packages.0.status}}
{{package_tracking.packages.1.label}} {{package_tracking.packages.1.status}}
{{package_tracking.packages.2.label}} {{package_tracking.packages.2.status}}
```

Which shows as:

```
PACKAGES
MOM'S GIFT  DELIVERED
SHOES  OUT FOR DELIV
LAPTOP     IN TRANSIT
```

**Tip:** `{{package_tracking.next_status}}` has built-in color rules — green for Delivered,
yellow for Out for Delivery, red for Exception, blue for In Transit. The colored tile appears
automatically, no template syntax needed.

## Configuration Reference

| Setting | Type | Required | Default | Description |
|---------|------|----------|---------|-------------|
| `enabled` | boolean | No | `false` | Enable or disable package tracking |
| `api_key` | password | **Yes** | — | Your 17TRACK security key |
| `tracking_numbers` | string | **Yes** | — | Up to 10 numbers, comma- or newline-separated |
| `auto_register` | boolean | No | `true` | Register new numbers with 17TRACK automatically |
| `refresh_seconds` | integer | No | `1800` | Poll interval in seconds (minimum 600) |

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PACKAGE_TRACKING_ENABLED` | No | `false` | Enable package tracking feature |
| `PACKAGE_TRACKING_API_KEY` | No | — | 17TRACK key, instead of the UI setting |

### Status Values

| Board shows | 17TRACK status |
|-------------|----------------|
| Pending | `NotFound`, `InfoReceived` |
| In Transit | `InTransit` |
| Out for Delivery | `OutForDelivery` |
| Ready for Pickup | `AvailableForPickup` |
| Delivery Failed | `DeliveryFailure` |
| Delivered | `Delivered` |
| Exception | `Exception` |
| Expired | `Expired` |

## API Information

This plugin uses the [17TRACK API v2.2](https://api.17track.net/en/doc):

- **Endpoints:** `POST /track/v2.2/register`, `POST /track/v2.2/gettrackinfo`
- **Authentication:** `17token` header with your security key
- **Rate Limits:** free tier ≈ 100 registrations/month; polling registered numbers is free
- **Format:** JSON array of `{"number": "..."}` in, `{"code": 0, "data": {...}}` out

### Sample API Response

```json
{
  "code": 0,
  "data": {
    "accepted": [
      {
        "number": "1Z999AA10123456784",
        "track_info": {
          "latest_status": { "status": "InTransit" },
          "latest_event": {
            "description": "Arrived at facility",
            "time_iso": "2025-09-13T16:12:00-05:00"
          },
          "tracking": {
            "providers": [{ "provider": { "name": "UPS" }, "events": [] }]
          }
        }
      }
    ],
    "rejected": []
  }
}
```

## Troubleshooting

### Plugin shows "Not Available"

1. **Check the API key:**
   ```bash
   curl -X POST https://api.17track.net/track/v2.2/gettrackinfo \
     -H "17token: YOUR_KEY" -H "Content-Type: application/json" \
     -d '[{"number":"1Z999AA10123456784"}]'
   ```
   A `code` other than `0` means the key is wrong or your quota is exhausted.

2. **Check logs:**
   ```bash
   docker-compose logs | grep -i package_tracking
   ```

### All packages show "Pending"

The numbers are registered but 17TRACK has no carrier data yet. This is normal for the first few
hours after a label is created. If it persists, confirm the number is correct — 17TRACK cannot
detect the carrier for every format, and a mistyped number simply never resolves.

### A number is never registered

If `auto_register` is off, register the numbers yourself in the 17TRACK dashboard. If it is on and
registration keeps failing, you have likely hit the free monthly quota.

## Restart After Changes

After changing Package Tracking settings, restart the service:

```bash
docker-compose restart
docker-compose logs -f
```

## Summary

- **Get a key**: api.17track.net → Settings → Security Key
- **Enable**: `PACKAGE_TRACKING_ENABLED=true` plus the key
- **Add numbers**: `Mom's gift:1Z999AA10123456784`, up to 10
- **Use in pages**: `{{package_tracking.packages.0.label}} {{package_tracking.packages.0.status}}`

📦
