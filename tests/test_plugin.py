"""Unit tests for the Package Tracking plugin."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.devices import BoardContext

from plugins.package_tracking import (
    ALREADY_REGISTERED_CODE,
    PackageTrackingPlugin,
    Plugin,
    _fit_status,
    format_short_time,
    normalize_status,
    parse_tracking_numbers,
    urgency,
)

MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "manifest.json").read_text())

DECLARED_SIMPLE = set(MANIFEST["variables"]["simple"])
DECLARED_ITEM_FIELDS = set(MANIFEST["variables"]["arrays"]["packages"]["item_fields"])


def make_plugin(**config):
    """Plugin instance backed by the real manifest, with config applied."""
    plugin = PackageTrackingPlugin(manifest=MANIFEST)
    plugin.config = {
        "api_key": "test-key",
        "tracking_numbers": "1Z999AA10123456784",
        **config,
    }
    return plugin


def track_info(status="InTransit", description="Arrived at facility", days_ago=1, carrier="UPS"):
    """A single accepted gettrackinfo entry."""
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


def api_response(payload, status_code=200):
    """A mock requests.Response returning *payload*."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def ok(accepted=None, rejected=None):
    return api_response({"code": 0, "data": {"accepted": accepted or [], "rejected": rejected or []}})


class TestPluginBasics:
    def test_plugin_id(self):
        assert PackageTrackingPlugin(manifest={}).plugin_id == "package_tracking"
        assert Plugin is PackageTrackingPlugin

    def test_manifest_id_matches(self):
        assert MANIFEST["id"] == PackageTrackingPlugin(manifest={}).plugin_id


class TestParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1Z999", [("1Z999", "1Z999")]),
            ("A, B", [("A", "A"), ("B", "B")]),
            ("A\nB", [("A", "A"), ("B", "B")]),
            ("Mom's gift:1Z999AA10123456784", [("Mom's gift", "1Z999AA10123456784")]),
            ("Shoes: 1Z999 , B", [("Shoes", "1Z999"), ("B", "B")]),
            ("  ", []),
            ("Label:", []),
            (":1Z999", [("1Z999", "1Z999")]),
        ],
    )
    def test_parse_tracking_numbers(self, text, expected):
        got = [(e["label"], e["number"]) for e in parse_tracking_numbers(text)]
        assert got == expected


class TestStatusNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("NotFound", "Pending"),
            ("InfoReceived", "Pending"),
            ("InTransit", "In Transit"),
            ("OutForDelivery", "Out for Delivery"),
            ("AvailableForPickup", "Ready for Pickup"),
            ("DeliveryFailure", "Delivery Failed"),
            ("Delivered", "Delivered"),
            ("Exception", "Exception"),
            ("Expired", "Expired"),
            ("SomethingNew", "SomethingNew"),
            ("", "Pending"),
            (None, "Pending"),
        ],
    )
    def test_normalize_status(self, raw, expected):
        assert normalize_status(raw) == expected

    def test_urgency_ordering(self):
        assert urgency("Out for Delivery") < urgency("In Transit") < urgency("Pending") < urgency("Delivered")
        assert urgency("Unknown Status") > urgency("Delivered")

    def test_format_short_time(self):
        dt = datetime(2025, 9, 13, 16, 12, tzinfo=timezone.utc)
        assert format_short_time(dt) == "Sep 13 4:12PM"
        assert len(format_short_time(datetime(2025, 9, 13, 0, 5, tzinfo=timezone.utc))) <= 14


class TestValidateConfig:
    def test_missing_api_key(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        with patch.dict("os.environ", {}, clear=True):
            errors = plugin.validate_config({"tracking_numbers": "1Z999"})
        assert "17TRACK API key is required" in errors

    def test_missing_tracking_numbers(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        errors = plugin.validate_config({"api_key": "k", "tracking_numbers": ""})
        assert "At least one tracking number is required" in errors

    def test_too_many_tracking_numbers(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        errors = plugin.validate_config(
            {"api_key": "k", "tracking_numbers": ",".join(f"N{i}" for i in range(25))}
        )
        assert "Maximum 24 tracking numbers allowed" in errors

    def test_refresh_below_minimum(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        errors = plugin.validate_config({"api_key": "k", "tracking_numbers": "N", "refresh_seconds": 5})
        assert any("at least" in e for e in errors)

    def test_valid(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        errors = plugin.validate_config(
            {"api_key": "k", "tracking_numbers": "Gift:1Z999", "refresh_seconds": 1800}
        )
        assert errors == []

    def test_api_key_from_env(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        with patch.dict("os.environ", {"PACKAGE_TRACKING_API_KEY": "env-key"}):
            assert plugin.validate_config({"tracking_numbers": "1Z999"}) == []


class TestFetchData:
    @patch("plugins.package_tracking.requests.post")
    def test_success_returns_all_declared_variables(self, mock_post):
        plugin = make_plugin(tracking_numbers="Gift:1Z999AA10123456784")
        mock_post.side_effect = [
            ok(accepted=[{"number": "1Z999AA10123456784"}]),
            ok(accepted=[{"number": "1Z999AA10123456784", "track_info": track_info()}]),
        ]

        result = plugin.fetch_data()

        assert result.available, result.error
        assert set(result.data) == DECLARED_SIMPLE | {"packages"}
        package = result.data["packages"][0]
        assert set(package) == DECLARED_ITEM_FIELDS
        assert package["label"] == "Gift"
        assert package["number"] == "1Z999AA10123456784"
        assert package["carrier"] == "UPS"
        assert package["status"] == "In Transit"
        assert package["status_code"] == "InTransit"
        assert package["last_event"] == "Arrived at facility"
        assert package["days_in_transit"] == 4
        assert package["last_update"]

        assert result.data["count"] == 1
        assert result.data["in_transit_count"] == 1
        assert result.data["next_label"] == "Gift"
        assert result.data["next_status"] == "In Transit"

    @patch("plugins.package_tracking.requests.post")
    def test_register_is_called_then_gettrackinfo(self, mock_post):
        plugin = make_plugin()
        mock_post.side_effect = [ok(accepted=[{"number": "1Z999AA10123456784"}]), ok(accepted=[])]

        plugin.fetch_data()

        register_call, track_call = mock_post.call_args_list
        assert register_call.args[0].endswith("/register")
        assert track_call.args[0].endswith("/gettrackinfo")
        assert register_call.kwargs["json"] == [{"number": "1Z999AA10123456784"}]
        assert register_call.kwargs["headers"]["17token"] == "test-key"
        assert register_call.kwargs["timeout"] == 10

    @patch("plugins.package_tracking.requests.post")
    def test_register_skipped_when_auto_register_off(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.return_value = ok(accepted=[])

        plugin.fetch_data()

        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0].endswith("/gettrackinfo")

    @patch("plugins.package_tracking.requests.post")
    def test_already_registered_rejection_is_success(self, mock_post):
        plugin = make_plugin()
        mock_post.side_effect = [
            ok(
                rejected=[
                    {
                        "number": "1Z999AA10123456784",
                        "error": {"code": ALREADY_REGISTERED_CODE, "message": "already registered"},
                    }
                ]
            ),
            ok(accepted=[]),
            ok(accepted=[]),
        ]

        assert plugin.fetch_data().available
        # Second fetch does not re-register: the number is remembered.
        plugin.fetch_data()
        assert mock_post.call_count == 3

    @patch("plugins.package_tracking.requests.post")
    def test_other_rejection_is_logged_not_fatal(self, mock_post):
        plugin = make_plugin()
        mock_post.side_effect = [
            ok(rejected=[{"number": "1Z999AA10123456784", "error": {"code": -1, "message": "bad"}}]),
            ok(accepted=[]),
        ]

        result = plugin.fetch_data()

        assert result.available
        assert result.data["packages"][0]["status"] == "Pending"

    @patch("plugins.package_tracking.requests.post")
    def test_urgency_picks_next_package(self, mock_post):
        plugin = make_plugin(
            tracking_numbers="Gift:A\nShoes:B\nBook:C", auto_register=False
        )
        mock_post.return_value = ok(
            accepted=[
                {"number": "A", "track_info": track_info(status="Delivered")},
                {"number": "B", "track_info": track_info(status="OutForDelivery")},
                {"number": "C", "track_info": track_info(status="InTransit")},
            ]
        )

        data = plugin.fetch_data().data

        assert data["next_label"] == "Shoes"
        assert data["next_status"] == "Out for Delivery"
        assert data["count"] == 3
        assert data["delivered_count"] == 1
        assert data["in_transit_count"] == 1
        assert data["out_for_delivery_count"] == 1

    @patch("plugins.package_tracking.requests.post")
    def test_unknown_number_falls_back_to_pending(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.return_value = ok(accepted=[])

        package = plugin.fetch_data().data["packages"][0]

        assert package["status"] == "Pending"
        assert package["status_code"] == ""
        assert package["carrier"] == ""
        assert package["last_event"] == ""
        assert package["last_update"] == ""
        assert package["days_in_transit"] == 0

    @patch("plugins.package_tracking.requests.post")
    def test_malformed_response_does_not_raise(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.return_value = api_response({"code": 0, "data": {"accepted": [{"number": "1Z999AA10123456784"}]}})

        assert plugin.fetch_data().available

    @patch("plugins.package_tracking.requests.post")
    def test_unparseable_event_time_is_ignored(self, mock_post):
        plugin = make_plugin(auto_register=False)
        info = track_info()
        info["latest_event"]["time_iso"] = "not-a-date"
        info["tracking"]["providers"][0]["events"] = [{"time_iso": "nope"}]
        mock_post.return_value = ok(accepted=[{"number": "1Z999AA10123456784", "track_info": info}])

        package = plugin.fetch_data().data["packages"][0]

        assert package["last_update"] == ""
        assert package["days_in_transit"] == 0

    @patch("plugins.package_tracking.requests.post")
    def test_delivered_days_in_transit_stops_at_delivery(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.return_value = ok(
            accepted=[
                {
                    "number": "1Z999AA10123456784",
                    "track_info": track_info(status="Delivered", days_ago=10),
                }
            ]
        )

        assert plugin.fetch_data().data["packages"][0]["days_in_transit"] == 3

    @patch("plugins.package_tracking.requests.post")
    def test_http_error_makes_plugin_unavailable(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.side_effect = requests.RequestException("boom")

        result = plugin.fetch_data()

        assert not result.available
        assert "boom" in result.error

    @patch("plugins.package_tracking.requests.post")
    def test_api_error_code_makes_plugin_unavailable(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.return_value = api_response({"code": -18010012, "message": "invalid key"})

        result = plugin.fetch_data()

        assert not result.available
        assert "-18010012" in result.error

    def test_missing_api_key_makes_plugin_unavailable(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        plugin.config = {"tracking_numbers": "1Z999"}
        with patch.dict("os.environ", {}, clear=True):
            result = plugin.fetch_data()
        assert not result.available
        assert "API key" in result.error

    def test_no_tracking_numbers_makes_plugin_unavailable(self):
        plugin = PackageTrackingPlugin(manifest=MANIFEST)
        plugin.config = {"api_key": "k", "tracking_numbers": ""}
        result = plugin.fetch_data()
        assert not result.available
        assert "No tracking numbers" in result.error

    @patch("plugins.package_tracking.requests.post")
    def test_caps_at_max_packages(self, mock_post):
        # 24 is the largest board's usable rows (8x8 note array, 24 rows,
        # minus the "PACKAGES" header) -- see MAX_PACKAGES in __init__.py.
        plugin = make_plugin(
            tracking_numbers=",".join(f"N{i}" for i in range(30)), auto_register=False
        )
        mock_post.return_value = ok(accepted=[])

        assert plugin.fetch_data().data["count"] == 24


class TestConfigLifecycle:
    def test_new_api_key_clears_registrations(self):
        plugin = make_plugin()
        plugin._registered.add("1Z999AA10123456784")
        plugin.config = {"api_key": "other-key", "tracking_numbers": "1Z999AA10123456784"}
        assert plugin._registered == set()

    def test_cleanup_clears_registrations(self):
        plugin = make_plugin()
        plugin._registered.add("X")
        plugin.cleanup()
        assert plugin._registered == set()


class TestFormattedDisplay:
    @patch("plugins.package_tracking.requests.post")
    def test_display_shape(self, mock_post):
        plugin = make_plugin(
            tracking_numbers="Mom's gift:A\nHeadphones:B", auto_register=False
        )
        mock_post.return_value = ok(
            accepted=[
                {"number": "A", "track_info": track_info(status="Delivered")},
                {"number": "B", "track_info": track_info(status="OutForDelivery")},
            ]
        )

        lines = plugin.get_formatted_display()

        assert len(lines) == 6
        assert all(len(line) <= 22 for line in lines)
        assert lines[0] == "PACKAGES"
        assert lines[1].startswith("MOM'S GIFT") and lines[1].endswith("DELIVERED")
        assert lines[2].endswith("OUT FOR DELIVERY")

    @patch("plugins.package_tracking.requests.post")
    def test_display_truncates_long_labels(self, mock_post):
        plugin = make_plugin(
            tracking_numbers="A really long package label:A", auto_register=False
        )
        mock_post.return_value = ok(
            accepted=[{"number": "A", "track_info": track_info(status="OutForDelivery")}]
        )

        lines = plugin.get_formatted_display()

        assert all(len(line) <= 22 for line in lines)
        assert lines[1].endswith("OUT FOR DELIVERY")

    @patch("plugins.package_tracking.requests.post")
    def test_display_none_when_unavailable(self, mock_post):
        plugin = make_plugin(auto_register=False)
        mock_post.side_effect = requests.RequestException("down")

        assert plugin.get_formatted_display() is None

    @patch("plugins.package_tracking.requests.post")
    def test_note_board_abbreviates_status_instead_of_crushing_label(self, mock_post):
        """Regression test for the 15-column truncation bug (__init__.py:278-280).

        Before the fix, label_width = max(1, cols - len(status) - 1) floored
        to 1 for any real status this long ("OUT FOR DELIVERY" is 17 chars
        on a 15-column board), and the line was then blindly right-truncated
        to `cols`, cutting the STATUS mid-word: "S OUT FOR DELIV". The fix
        abbreviates the status first so the label keeps a readable width.
        """
        plugin = make_plugin(tracking_numbers="Shoes:A", auto_register=False)
        mock_post.return_value = ok(
            accepted=[{"number": "A", "track_info": track_info(status="OutForDelivery")}]
        )
        note = BoardContext(device_type="note", rows=3, cols=15)

        with plugin._bound_board(note):
            lines = plugin.get_formatted_display()

        assert all(len(line) <= 15 for line in lines)
        assert lines[1] == "SHOES OUT DLVRY"
        assert "DELIV" not in lines[1]  # the old mid-word truncation

    @patch("plugins.package_tracking.requests.post")
    def test_note_board_does_not_crush_label_to_one_character(self, mock_post):
        plugin = make_plugin(
            tracking_numbers="Mom's gift:A\nLaptop:B", auto_register=False
        )
        mock_post.return_value = ok(
            accepted=[
                {"number": "A", "track_info": track_info(status="AvailableForPickup")},
                {"number": "B", "track_info": track_info(status="DeliveryFailure")},
            ]
        )
        note = BoardContext(device_type="note", rows=3, cols=15)

        with plugin._bound_board(note):
            lines = plugin.get_formatted_display()

        assert all(len(line) <= 15 for line in lines)
        assert lines[1] == "MOM'S GIF READY"
        assert lines[2] == "LAPTOP   FAILED"
        # Neither label was crushed to a single character (the old bug).
        assert lines[1].split()[0] == "MOM'S"
        assert lines[2].startswith("LAPTOP")


class TestFitStatus:
    def test_short_status_passes_through_unchanged(self):
        assert _fit_status("In Transit", budget=11) == "IN TRANSIT"

    def test_long_status_abbreviates_rather_than_truncates_midword(self):
        assert _fit_status("Out for Delivery", budget=11) == "OUT DLVRY"
        assert _fit_status("Ready for Pickup", budget=11) == "READY"
        assert _fit_status("Delivery Failed", budget=11) == "FAILED"

    def test_falls_back_to_truncation_if_even_abbreviation_does_not_fit(self):
        assert _fit_status("Out for Delivery", budget=3) == "OUT"
