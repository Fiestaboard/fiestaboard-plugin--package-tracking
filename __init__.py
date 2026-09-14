"""Package Tracking plugin for FiestaBoard.

Tracks shipments through the 17TRACK API and exposes each package's
carrier, status and latest event as template variables.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging
import os

import requests

from src.plugins.base import PluginBase, PluginResult

logger = logging.getLogger(__name__)

API_BASE = "https://api.17track.net/track/v2.2/"
REGISTER_URL = API_BASE + "register"
TRACKINFO_URL = API_BASE + "gettrackinfo"
USER_AGENT = "FiestaBoard (https://github.com/FiestaBoard/FiestaBoard)"

MAX_PACKAGES = 10
LAST_EVENT_MAX_LENGTH = 44
ALREADY_REGISTERED_CODE = -18019901

# 17TRACK raw status -> display status
STATUS_MAP = {
    "NotFound": "Pending",
    "InfoReceived": "Pending",
    "InTransit": "In Transit",
    "OutForDelivery": "Out for Delivery",
    "AvailableForPickup": "Ready for Pickup",
    "DeliveryFailure": "Delivery Failed",
    "Delivered": "Delivered",
    "Exception": "Exception",
    "Expired": "Expired",
}

# Most urgent first; decides which package becomes next_label / next_status
URGENCY_ORDER = [
    "Out for Delivery",
    "Ready for Pickup",
    "Delivery Failed",
    "Exception",
    "In Transit",
    "Pending",
    "Expired",
    "Delivered",
]


def normalize_status(raw: Optional[str]) -> str:
    """Map a raw 17TRACK status to the display status."""
    if not raw:
        return "Pending"
    return STATUS_MAP.get(raw, raw)


def urgency(status: str) -> int:
    """Lower is more urgent. Unknown statuses sort last."""
    if status in URGENCY_ORDER:
        return URGENCY_ORDER.index(status)
    return len(URGENCY_ORDER)


def parse_tracking_numbers(text: str) -> List[Dict[str, str]]:
    """Parse comma- or newline-separated entries, each `number` or `label:number`."""
    entries = []
    for chunk in text.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        label, sep, number = chunk.partition(":")
        if not sep:
            label, number = chunk, chunk
        label, number = label.strip(), number.strip()
        if not number:
            continue
        entries.append({"label": label or number, "number": number})
    return entries


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_short_time(dt: datetime) -> str:
    """Render like 'Sep 13 4:12PM'."""
    return f"{dt:%b} {dt.day} {dt.hour % 12 or 12}:{dt:%M}{dt:%p}"


class PackageTrackingPlugin(PluginBase):
    """Package Tracking plugin.

    Registers the configured tracking numbers with 17TRACK (once per
    number) and fetches their latest status on each refresh.
    """

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._registered: set = set()

    @property
    def plugin_id(self) -> str:
        return "package_tracking"

    def _api_key(self, config: Optional[Dict[str, Any]] = None) -> str:
        config = self.config if config is None else config
        return (config.get("api_key") or os.getenv("PACKAGE_TRACKING_API_KEY", "")).strip()

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors = []

        if not self._api_key(config):
            errors.append("17TRACK API key is required")

        entries = parse_tracking_numbers(config.get("tracking_numbers") or "")
        if not entries:
            errors.append("At least one tracking number is required")
        elif len(entries) > MAX_PACKAGES:
            errors.append(f"Maximum {MAX_PACKAGES} tracking numbers allowed")

        errors.extend(self._validate_refresh_seconds(config))
        return errors

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        # Registrations belong to the API account; a new key means a new account.
        if old_config.get("api_key") != new_config.get("api_key"):
            self._registered.clear()

    def cleanup(self) -> None:
        self._registered.clear()

    def _post(self, url: str, numbers: List[str]) -> Dict[str, Any]:
        """POST a list of tracking numbers and return the response `data` object."""
        response = requests.post(
            url,
            json=[{"number": n} for n in numbers],
            headers={
                "17token": self._api_key(),
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(
                f"17TRACK error {body.get('code')}: {body.get('message', 'unknown error')}"
            )
        return body.get("data") or {}

    def _register(self, numbers: List[str]) -> None:
        """Register numbers 17TRACK has not seen from us yet."""
        pending = [n for n in numbers if n not in self._registered]
        if not pending:
            return

        data = self._post(REGISTER_URL, pending)
        for item in data.get("accepted") or []:
            self._registered.add(item.get("number"))
        for item in data.get("rejected") or []:
            error = item.get("error") or {}
            if error.get("code") == ALREADY_REGISTERED_CODE:
                self._registered.add(item.get("number"))
            else:
                logger.warning(
                    "17TRACK rejected %s: %s", item.get("number"), error.get("message")
                )

    @staticmethod
    def _build_package(entry: Dict[str, str], track_info: Dict[str, Any]) -> Dict[str, Any]:
        latest_status = track_info.get("latest_status") or {}
        latest_event = track_info.get("latest_event") or {}
        if isinstance(latest_event, str):
            latest_event = {"description": latest_event}

        providers = (track_info.get("tracking") or {}).get("providers") or []
        carrier = ""
        events: List[Dict[str, Any]] = []
        if providers:
            carrier = ((providers[0].get("provider") or {}).get("name")) or ""
            for provider in providers:
                events.extend(provider.get("events") or [])

        raw_status = latest_status.get("status") or ""
        status = normalize_status(raw_status)

        latest_time = _parse_time(latest_event.get("time_iso") or latest_event.get("time_utc"))
        event_times = [
            t for t in (_parse_time(ev.get("time_iso") or ev.get("time_utc")) for ev in events) if t
        ]
        days_in_transit = 0
        if event_times:
            if status == "Delivered" and latest_time:
                end = latest_time
            else:
                end = datetime.now(timezone.utc)
            days_in_transit = max(0, (end - min(event_times)).days)

        return {
            "label": entry["label"],
            "number": entry["number"],
            "carrier": carrier,
            "status": status,
            "status_code": raw_status,
            "last_event": (latest_event.get("description") or "")[:LAST_EVENT_MAX_LENGTH],
            "last_update": format_short_time(latest_time) if latest_time else "",
            "days_in_transit": days_in_transit,
        }

    def fetch_data(self) -> PluginResult:
        """Fetch the latest status for every configured tracking number."""
        try:
            if not self._api_key():
                return PluginResult(available=False, error="17TRACK API key not configured")

            entries = parse_tracking_numbers(self.config.get("tracking_numbers") or "")
            entries = entries[:MAX_PACKAGES]
            if not entries:
                return PluginResult(available=False, error="No tracking numbers configured")

            numbers = [e["number"] for e in entries]
            if self.config.get("auto_register", True):
                self._register(numbers)

            data = self._post(TRACKINFO_URL, numbers)
            info_by_number = {
                item.get("number"): item.get("track_info") or {}
                for item in data.get("accepted") or []
            }

            packages = [
                self._build_package(entry, info_by_number.get(entry["number"], {}))
                for entry in entries
            ]
            statuses = [p["status"] for p in packages]
            next_package = min(packages, key=lambda p: urgency(p["status"]))

            return PluginResult(
                available=True,
                data={
                    "packages": packages,
                    "count": len(packages),
                    "delivered_count": statuses.count("Delivered"),
                    "in_transit_count": statuses.count("In Transit"),
                    "out_for_delivery_count": statuses.count("Out for Delivery"),
                    "next_label": next_package["label"],
                    "next_status": next_package["status"],
                },
            )

        except Exception as e:
            logger.exception("Error fetching package tracking data")
            return PluginResult(available=False, error=str(e))

    def get_formatted_display(self) -> Optional[List[str]]:
        """Default display: PACKAGES header, then one 'LABEL STATUS' line per package."""
        result = self.get_data()
        if not result.available or not result.data:
            return None

        rows, cols = (self.board.rows, self.board.cols) if self.board else (6, 22)

        lines = ["PACKAGES"]
        for package in result.data["packages"][: rows - 1]:
            status = package["status"].upper()
            label_width = max(1, cols - len(status) - 1)
            label = package["label"].upper()[:label_width]
            lines.append(f"{label:<{label_width}} {status}"[:cols])

        while len(lines) < rows:
            lines.append("")

        return lines


# Export the plugin class
Plugin = PackageTrackingPlugin
