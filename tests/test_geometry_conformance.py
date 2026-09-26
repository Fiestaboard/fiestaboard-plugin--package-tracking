"""Board-geometry conformance for the Package Tracking plugin.

Runs the shared FiestaBoard-core suite (imported directly -- core is on
PYTHONPATH in plugin CI) against every supported board shape: Flagship,
Note, and note arrays from 15x3 up to 120x24 (a FiestaPanel is a note
array sized to a TV; there is no separate panel API).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.plugins.geometry_conformance import assert_board_conformance

from plugins.package_tracking import PackageTrackingPlugin

MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text())

# A spread of packages that includes the long display statuses that caused
# the label-crushing bug ("Out for Delivery" / "Ready for Pickup", both 17
# characters -- wider than a Note's 15 columns) and more than the *old*
# MAX_PACKAGES=10 ceiling, so a fix that only raised the number without
# actually wiring it through would still show up here.
_PACKAGE_FIXTURES = [
    ("Shoes", "1Z999AA10123456781", "OutForDelivery", "Courier en route"),
    ("Mom's gift", "1Z999AA10123456782", "AvailableForPickup", "Ready at locker"),
    ("Laptop", "1Z999AA10123456783", "DeliveryFailure", "No access to building"),
    ("Headphones", "1Z999AA10123456784", "InTransit", "Departed sort facility"),
    ("Book", "1Z999AA10123456785", "Delivered", "Left at front door"),
    ("Charger", "1Z999AA10123456786", "Exception", "Address issue"),
    ("Desk lamp", "1Z999AA10123456787", "NotFound", ""),
    ("Keyboard", "1Z999AA10123456788", "InTransit", "Arrived at facility"),
    ("Monitor", "1Z999AA10123456789", "OutForDelivery", "Courier en route"),
    ("Mouse", "1Z999AA10123456790", "Delivered", "Left with neighbor"),
    ("Webcam", "1Z999AA10123456791", "InTransit", "In transit to next facility"),
    ("Mic stand", "1Z999AA10123456792", "AvailableForPickup", "Ready at locker"),
]


def _track_info(status: str, description: str, days_ago: int = 1, carrier: str = "UPS") -> dict:
    """A single accepted gettrackinfo entry (mirrors tests/test_plugin.py's track_info)."""
    now = datetime.now(timezone.utc)
    return {
        "latest_status": {"status": status},
        "latest_event": {
            "description": description,
            "time_iso": (now - timedelta(days=days_ago)).isoformat(),
        },
        "tracking": {
            "providers": [
                {
                    "provider": {"name": carrier},
                    "events": [
                        {
                            "description": "Shipment received",
                            "time_iso": (now - timedelta(days=days_ago + 3)).isoformat(),
                        },
                        {
                            "description": description,
                            "time_iso": (now - timedelta(days=days_ago)).isoformat(),
                        },
                    ],
                }
            ]
        },
    }


def _stubbed_response() -> MagicMock:
    accepted = [
        {"number": number, "track_info": _track_info(status, description)}
        for _, number, status, description in _PACKAGE_FIXTURES
    ]
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": 0, "data": {"accepted": accepted, "rejected": []}}
    return response


def make_plugin() -> PackageTrackingPlugin:
    """A fresh, configured plugin. Network access is stubbed by the caller
    (the `requests.post` patch below), not here -- the suite renders this
    instance many times and never touches the network.
    """
    plugin = PackageTrackingPlugin(manifest=MANIFEST)
    plugin.config = {
        "api_key": "test-key",
        "tracking_numbers": "\n".join(
            f"{label}:{number}" for label, number, _, _ in _PACKAGE_FIXTURES
        ),
        "auto_register": False,
    }
    return plugin


@patch("plugins.package_tracking.requests.post")
def test_renders_on_every_board_shape(mock_post):
    mock_post.return_value = _stubbed_response()

    assert_board_conformance(
        make_plugin,
        manifest=MANIFEST,
        # This plugin renders a package list -- more rows must mean more
        # visible packages whenever the shorter board was full.
        strict_growth=True,
        require_note_array_preview=True,
    )
