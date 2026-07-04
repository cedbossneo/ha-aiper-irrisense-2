"""Tests for the coordinator's pure logic: zone id extraction, preset
snapping, dashboard selection state, the active-zone snapshot, and MQTT
message bucketing."""
from __future__ import annotations

import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense.api import IrrisenseApi
from custom_components.aiper_irrisense.const import (
    DOMAIN,
    POINT_TIME_PRESETS,
    WATER_YIELD_PRESETS,
)
from custom_components.aiper_irrisense.coordinator import (
    IrrisenseCoordinator,
    _extract_map_id,
    _snap_to_preset,
)


def _make_coordinator(hass) -> IrrisenseCoordinator:
    api = IrrisenseApi("u", "p", "eu")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "region": "eu"},
        options={},
    )
    entry.add_to_hass(hass)
    return IrrisenseCoordinator(hass, api, entry)


# --------------------------------------------------------------------------- #
# _extract_map_id
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"map_id": 3}, 3),
        ({"mapId": "4"}, 4),           # legacy camelCase + string coercion
        ({"region_id": 5}, 5),         # older spelling
        ({"map_info": {"id": 7}}, 7),  # realTimeProgress nesting
        ({"status": 1, "mode": 0}, None),  # workInfo — no zone id
        ({"map_id": "notint"}, None),
        ("not-a-dict", None),
    ],
)
def test_extract_map_id(body, expected) -> None:
    assert _extract_map_id(body) == expected


# --------------------------------------------------------------------------- #
# _snap_to_preset
# --------------------------------------------------------------------------- #
def test_snap_to_preset_exact_value_passthrough() -> None:
    assert _snap_to_preset(0.25, WATER_YIELD_PRESETS, "wy") == 0.25


def test_snap_to_preset_snaps_off_preset() -> None:
    assert _snap_to_preset(0.2, WATER_YIELD_PRESETS, "wy") == 0.25
    assert _snap_to_preset(0.11, WATER_YIELD_PRESETS, "wy") == 0.1
    assert _snap_to_preset(7, POINT_TIME_PRESETS, "pt") == 5


# --------------------------------------------------------------------------- #
# zones_for / zone_name / _region_for
# --------------------------------------------------------------------------- #
def test_zone_lookups(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {
        "map": {"regions": [{"id": 1, "name": "Lawn"}, {"id": 2, "name": "Bed"}]}
    }
    assert [z["id"] for z in coord.zones_for("SN")] == [1, 2]
    assert coord.zone_name("SN", 2) == "Bed"
    assert coord.zone_name("SN", 99) is None
    assert coord._region_for("SN", 1) == {"id": 1, "name": "Lawn"}
    assert coord._region_for("SN", 99) is None


def test_zones_for_unknown_device_is_empty(hass) -> None:
    coord = _make_coordinator(hass)
    assert coord.zones_for("missing") == []


# --------------------------------------------------------------------------- #
# Dashboard selection state
# --------------------------------------------------------------------------- #
def test_selection_state_defaults_and_updates(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {
        "map": {
            "regions": [
                {"id": 1, "name": "Lawn", "type": 0},
                {"id": 2, "name": "Bed", "type": 2},
            ]
        }
    }

    # No explicit pick yet → falls back to the first zone.
    assert coord.get_zone_selection("SN") == 1

    # Picking a Point zone resets dose to its type default ("1 min").
    coord.set_zone_selection("SN", 2)
    assert coord.get_zone_selection("SN") == 2
    assert coord.get_dose_selection("SN") == "1 min"
    assert coord.selected_region_type("SN") == 2

    # Explicit dose pick persists.
    coord.set_dose_selection("SN", "5 min")
    assert coord.get_dose_selection("SN") == "5 min"


def test_get_zone_selection_ignores_stale_pick(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {"map": {"regions": [{"id": 1, "type": 0}]}}
    coord._zone_selection["SN"] = 999  # no longer a valid zone
    assert coord.get_zone_selection("SN") == 1  # falls back to first


# --------------------------------------------------------------------------- #
# active_zone_state
# --------------------------------------------------------------------------- #
def _region(**kw):
    base = {
        "id": 1,
        "name": "Zone",
        "type": 0,
        "waterYield": 0.1,
        "pointTime": 1,
        "n_points": 0,
    }
    base.update(kw)
    return base


def test_active_zone_state_none_without_frames(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {"map": {"regions": []}, "mqtt": {}}
    assert coord.active_zone_state("SN") is None


def test_active_zone_state_none_when_stopped(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {
        "map": {"regions": [_region()]},
        "mqtt": {
            "up_realTimeProgress": {
                "data": {"status": 0, "map_info": {"id": 1}},
                "_ts": time.time(),
            }
        },
    }
    assert coord.active_zone_state("SN") is None


def test_active_zone_state_running_area(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {
        "map": {"regions": [_region(id=1, name="Lawn", type=0, waterYield=0.1)]},
        "mqtt": {
            "up_realTimeProgress": {
                "data": {
                    "status": 1,
                    "map_info": {"id": 1},
                    "time": 30,
                    "progress": 10,
                },
                "_ts": time.time(),
            }
        },
    }
    state = coord.active_zone_state("SN")
    assert state is not None
    assert state["is_running"] is True
    assert state["zone_id"] == 1
    assert state["zone_name"] == "Lawn"
    assert state["region_type"] == 0
    assert state["time_sec"] == 30
    assert state["dose_label"] == "3 mm"
    # Back-solved from elapsed / progress: 30 / 0.10 = 300s, and latched
    # (progress 10 >= 5) so it is not the pending placeholder.
    assert state["duration_seconds"] == 300
    assert state["duration_pending"] is False


def test_active_zone_state_running_point_via_setworkmode(hass) -> None:
    coord = _make_coordinator(hass)
    coord._data["SN"] = {
        "map": {"regions": [_region(id=2, name="Bed", type=2, pointTime=5, n_points=3)]},
        "mqtt": {
            "up_setWorkMode": {
                "data": {"status": 1, "map_id": 2, "point_time": 5},
                "_ts": time.time(),
            }
        },
    }
    state = coord.active_zone_state("SN")
    assert state is not None
    assert state["zone_id"] == 2
    assert state["region_type"] == 2
    assert state["point_time"] == 5
    assert state["dose_label"] == "5 min"
    # Point duration: pointTime(5) * n_points(3) * 60 = 900s.
    assert state["duration_seconds"] == 900


def test_active_zone_state_prefers_freshest_frame(hass) -> None:
    coord = _make_coordinator(hass)
    now = time.time()
    coord._data["SN"] = {
        "map": {"regions": [_region(id=1), _region(id=2, name="Two")]},
        "mqtt": {
            # Older frame says zone 1 running...
            "up_workInfo": {
                "data": {"status": 1, "map_id": 1},
                "_ts": now - 10,
            },
            # ...but the freshest frame is zone 2.
            "up_realTimeProgress": {
                "data": {"status": 1, "map_info": {"id": 2}, "time": 5, "progress": 3},
                "_ts": now,
            },
        },
    }
    state = coord.active_zone_state("SN")
    assert state is not None
    assert state["zone_id"] == 2


# --------------------------------------------------------------------------- #
# handle_mqtt_message bucketing
# --------------------------------------------------------------------------- #
async def test_handle_mqtt_message_upchan_new_shape(hass) -> None:
    coord = _make_coordinator(hass)
    # Arm a pending ACK so we can prove the echo clears it.
    coord.api._pending_ack[("SN", "setWorkMode")] = time.time()

    coord.handle_mqtt_message(
        "SN",
        {
            "_topic": "aiper/things/SN/upChan",
            "setWorkMode": {"status": 1, "map_id": 1, "waterYield": 0.1},
        },
    )
    await hass.async_block_till_done()

    slot = coord._data["SN"]["mqtt"]["up_setWorkMode"]
    assert slot["type"] == "setWorkMode"
    assert slot["data"]["map_id"] == 1
    assert "_ts" in slot
    # ACK cleared + fast-poll window opened.
    assert ("SN", "setWorkMode") not in coord.api._pending_ack
    assert coord.update_interval == coord._fast_interval


async def test_handle_mqtt_message_legacy_shape(hass) -> None:
    coord = _make_coordinator(hass)
    coord.handle_mqtt_message(
        "SN",
        {
            "_topic": "aiper/things/SN/upChan",
            "type": "up_workInfo",
            "data": {"status": 1},
        },
    )
    await hass.async_block_till_done()
    assert coord._data["SN"]["mqtt"]["up_workInfo"]["data"] == {"status": 1}


async def test_handle_mqtt_message_status0_clears_setworkmode_ack(hass) -> None:
    coord = _make_coordinator(hass)
    coord.api._pending_ack[("SN", "setWorkMode")] = time.time()
    coord.handle_mqtt_message(
        "SN",
        {
            "_topic": "aiper/things/SN/upChan",
            "realTimeProgress": {"status": 0},
        },
    )
    await hass.async_block_till_done()
    # A status:0 realtime frame is treated as the ACK for a pending stop.
    assert ("SN", "setWorkMode") not in coord.api._pending_ack


async def test_handle_mqtt_message_buckets_by_topic(hass) -> None:
    coord = _make_coordinator(hass)
    coord.handle_mqtt_message(
        "SN", {"_topic": "$aws/things/SN/shadow/get/accepted", "state": {}}
    )
    coord.handle_mqtt_message(
        "SN", {"_topic": "aiper/things/SN/WR/cloud/report", "alarm": 1}
    )
    await hass.async_block_till_done()
    mqtt = coord._data["SN"]["mqtt"]
    assert "shadow_get" in mqtt
    assert mqtt["cloud_report"]["alarm"] == 1


def test_handle_mqtt_message_ignores_non_dict(hass) -> None:
    coord = _make_coordinator(hass)
    coord.handle_mqtt_message("SN", "not-a-dict")  # must not raise
    assert "SN" not in coord._data or "mqtt" not in coord._data.get("SN", {})
