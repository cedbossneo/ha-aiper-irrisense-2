"""Unit tests for the sensor platform.

Entities are exercised directly against a seeded coordinator. Active-run
sensors read ``coordinator.active_zone_state`` — monkeypatched per-test to
drive the running/idle branches deterministically — while the firmware,
WiFi, totals and history sensors read the seeded ``coordinator.data`` slot.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense.api import IrrisenseApi
from custom_components.aiper_irrisense.const import DOMAIN
from custom_components.aiper_irrisense.coordinator import IrrisenseCoordinator
from custom_components.aiper_irrisense.sensor import (
    ActiveElapsedSensor,
    ActiveProgressSensor,
    ActiveRepairLayerSensor,
    ActiveTotalSensor,
    ActiveZoneSensor,
    FirmwareSensor,
    LastWateringZoneSensor,
    McuFirmwareSensor,
    TotalWaterSavingSensor,
    TotalWateringEventsSensor,
    TotalWaterYieldSensor,
    ValveFirmwareSensor,
    WifiRssiSensor,
    async_setup_entry,
)


def make_coordinator(hass, data=None) -> IrrisenseCoordinator:
    api = IrrisenseApi("u", "p", "eu")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "region": "eu"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = IrrisenseCoordinator(hass, api, entry)
    if data is not None:
        coord.data = data
        coord._data = data
    coord.last_update_success = True
    return coord


def _set_active(coord, state):
    """Force active_zone_state to return a fixed snapshot for any sn."""
    coord.active_zone_state = lambda sn: state


# --------------------------------------------------------------------------- #
# ActiveZoneSensor
# --------------------------------------------------------------------------- #
def test_active_zone_running_with_name(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "zone_name": "Lawn", "zone_id": 1})
    ent = ActiveZoneSensor(coord, "SN")
    assert ent.native_value == "Lawn"


def test_active_zone_running_without_name_uses_id(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "zone_id": 3})
    ent = ActiveZoneSensor(coord, "SN")
    assert ent.native_value == "Zone 3"


def test_active_zone_running_without_name_or_id(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True})
    ent = ActiveZoneSensor(coord, "SN")
    assert ent.native_value == "Running"


@pytest.mark.parametrize("state", [None, {"is_running": False}])
def test_active_zone_idle(hass, state) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, state)
    ent = ActiveZoneSensor(coord, "SN")
    assert ent.native_value == "Idle"
    assert ent.extra_state_attributes == {"is_running": False}


def test_active_zone_attributes_full(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    start_ts = 1_700_000_000
    _set_active(
        coord,
        {
            "is_running": True,
            "zone_id": 2,
            "zone_name": "Bed",
            "region_type": 2,
            "dose_label": "5 min",
            "water_yield": 0.1,
            "point_time": 5,
            "time_sec": 42,
            "progress": 50,
            "x": 1.0,
            "y": 2.0,
            "repair_layer": 3,
            "source": "realTimeProgress",
            "start_ts": start_ts,
            "duration_seconds": 300,
            "duration_hms": "0:05:00",
            "duration_pending": False,
        },
    )
    attrs = ActiveZoneSensor(coord, "SN").extra_state_attributes
    assert attrs["is_running"] is True
    assert attrs["region_type_label"] == "Point"
    assert attrs["elapsed_seconds"] == 42
    assert attrs["duration_hms"] == "0:05:00"
    expected_iso = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
    assert attrs["start_time"] == expected_iso


@pytest.mark.parametrize(
    ("rtype", "label"),
    [(0, "Area"), (1, "Line"), (2, "Point"), (None, None), (9, None)],
)
def test_active_zone_region_type_label(hass, rtype, label) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "region_type": rtype})
    attrs = ActiveZoneSensor(coord, "SN").extra_state_attributes
    assert attrs["region_type_label"] == label
    # No numeric start_ts → start_time stays None.
    assert attrs["start_time"] is None


# --------------------------------------------------------------------------- #
# ActiveElapsedSensor
# --------------------------------------------------------------------------- #
def test_active_elapsed_running(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "time_sec": 42.7})
    assert ActiveElapsedSensor(coord, "SN").native_value == 42


def test_active_elapsed_idle_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, None)
    assert ActiveElapsedSensor(coord, "SN").native_value is None


def test_active_elapsed_non_numeric_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "time_sec": "nope"})
    assert ActiveElapsedSensor(coord, "SN").native_value is None


# --------------------------------------------------------------------------- #
# ActiveTotalSensor
# --------------------------------------------------------------------------- #
def test_active_total_running(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "duration_seconds": 300})
    assert ActiveTotalSensor(coord, "SN").native_value == 300


def test_active_total_zero_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "duration_seconds": 0})
    assert ActiveTotalSensor(coord, "SN").native_value is None


def test_active_total_idle_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": False})
    assert ActiveTotalSensor(coord, "SN").native_value is None


# --------------------------------------------------------------------------- #
# ActiveProgressSensor
# --------------------------------------------------------------------------- #
def test_active_progress_fraction_scaled(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "progress": 0.5})
    assert ActiveProgressSensor(coord, "SN").native_value == 50.0


def test_active_progress_already_scaled_and_clamped(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "progress": 150})
    assert ActiveProgressSensor(coord, "SN").native_value == 100.0


def test_active_progress_fallback_from_elapsed_and_point_time(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    # No progress: 30s elapsed / (1 min * 60 = 60s) = 50%.
    _set_active(coord, {"is_running": True, "time_sec": 30, "point_time": 1})
    assert ActiveProgressSensor(coord, "SN").native_value == 50.0


def test_active_progress_no_data_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True})
    assert ActiveProgressSensor(coord, "SN").native_value is None


def test_active_progress_idle_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, None)
    assert ActiveProgressSensor(coord, "SN").native_value is None


# --------------------------------------------------------------------------- #
# ActiveRepairLayerSensor
# --------------------------------------------------------------------------- #
def test_active_repair_layer_running(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "repair_layer": 4.0})
    assert ActiveRepairLayerSensor(coord, "SN").native_value == 4


def test_active_repair_layer_non_numeric_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, {"is_running": True, "repair_layer": None})
    assert ActiveRepairLayerSensor(coord, "SN").native_value is None


def test_active_repair_layer_idle_none(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    _set_active(coord, None)
    assert ActiveRepairLayerSensor(coord, "SN").native_value is None


# --------------------------------------------------------------------------- #
# Firmware sensors
# --------------------------------------------------------------------------- #
def test_firmware_from_wr_info(hass) -> None:
    coord = make_coordinator(
        hass, {"SN": {"wr_info": {"mainFirmwareVersion": "1.2.3"}}}
    )
    assert FirmwareSensor(coord, "SN").native_value == "1.2.3"


def test_firmware_fallback_to_equipment_version(hass) -> None:
    coord = make_coordinator(
        hass, {"SN": {"wr_info": {}, "equipment": {"version": "9.9"}}}
    )
    assert FirmwareSensor(coord, "SN").native_value == "9.9"


def test_firmware_none_when_missing(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    # No wr_info, no fallback key set.
    assert McuFirmwareSensor(coord, "SN").native_value is None


def test_mcu_and_valve_firmware(hass) -> None:
    coord = make_coordinator(
        hass,
        {
            "SN": {
                "wr_info": {
                    "mcuFirmwareVersion": "mcu-1",
                    "valveFirmwareVersion": "valve-2",
                }
            }
        },
    )
    assert McuFirmwareSensor(coord, "SN").native_value == "mcu-1"
    assert ValveFirmwareSensor(coord, "SN").native_value == "valve-2"


def test_firmware_ignores_non_string(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"wr_info": {"mainFirmwareVersion": 123}}})
    assert FirmwareSensor(coord, "SN").native_value is None


# --------------------------------------------------------------------------- #
# WifiRssiSensor
# --------------------------------------------------------------------------- #
def test_wifi_rssi_value_and_ssid(hass) -> None:
    coord = make_coordinator(
        hass, {"SN": {"equipment": {"wifiRssi": -55, "wifiName": "MyNet"}}}
    )
    ent = WifiRssiSensor(coord, "SN")
    assert ent.native_value == -55
    assert ent.extra_state_attributes == {"ssid": "MyNet"}


def test_wifi_rssi_missing(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"equipment": {}}})
    ent = WifiRssiSensor(coord, "SN")
    assert ent.native_value is None
    assert ent.extra_state_attributes is None


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #
def test_total_water_yield(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"stats": {"totalWaterYield": 12}}})
    assert TotalWaterYieldSensor(coord, "SN").native_value == 12.0


def test_total_water_saving(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"stats": {"totalWaterSavingAmount": 3.5}}})
    assert TotalWaterSavingSensor(coord, "SN").native_value == 3.5


def test_total_events(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"stats": {"totalRecordCount": 7}}})
    assert TotalWateringEventsSensor(coord, "SN").native_value == 7


def test_totals_none_without_stats(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    assert TotalWaterYieldSensor(coord, "SN").native_value is None
    assert TotalWaterSavingSensor(coord, "SN").native_value is None
    assert TotalWateringEventsSensor(coord, "SN").native_value is None


def test_total_events_ignores_non_int(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"stats": {"totalRecordCount": 7.5}}})
    assert TotalWateringEventsSensor(coord, "SN").native_value is None


# --------------------------------------------------------------------------- #
# LastWateringZoneSensor
# --------------------------------------------------------------------------- #
def test_last_zone_via_zone_name_lookup(hass) -> None:
    coord = make_coordinator(
        hass,
        {
            "SN": {
                "map": {"regions": [{"id": 5, "name": "Roses"}]},
                "history": {"list": [{"regionId": 5, "startTime": "t", "duration": 10}]},
            }
        },
    )
    ent = LastWateringZoneSensor(coord, "SN")
    assert ent.native_value == "Roses"
    attrs = ent.extra_state_attributes
    assert attrs["start_time"] == "t"
    assert attrs["duration_minutes"] == 10


def test_last_zone_fallback_to_record_name(hass) -> None:
    coord = make_coordinator(
        hass,
        {"SN": {"map": {"regions": []}, "history": {"records": [{"name": "Patio"}]}}},
    )
    assert LastWateringZoneSensor(coord, "SN").native_value == "Patio"


def test_last_zone_bad_region_id_falls_through(hass) -> None:
    coord = make_coordinator(
        hass,
        {
            "SN": {
                "map": {"regions": []},
                "history": {"data": [{"mapId": "x", "regionName": "Deck"}]},
            }
        },
    )
    assert LastWateringZoneSensor(coord, "SN").native_value == "Deck"


def test_last_zone_no_history(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    ent = LastWateringZoneSensor(coord, "SN")
    assert ent.native_value is None
    assert ent.extra_state_attributes is None


def test_last_zone_empty_list(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"history": {"list": []}}})
    ent = LastWateringZoneSensor(coord, "SN")
    assert ent.native_value is None
    assert ent.extra_state_attributes is None


# --------------------------------------------------------------------------- #
# available
# --------------------------------------------------------------------------- #
def test_available_reflects_update_success(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    ent = ActiveZoneSensor(coord, "SN")
    assert ent.available is True
    coord.last_update_success = False
    assert ent.available is False


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #
async def test_async_setup_entry_wires_entities(hass) -> None:
    coord = make_coordinator(hass, {"WR1": {}})
    # Two devices, one missing a sn (should be skipped).
    coord.api._devices = {"WR1": {"sn": "WR1"}, "bad": {}}
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coordinator": coord}

    added: list = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))

    # 14 sensor classes are wired per device with a sn; only WR1 qualifies.
    assert len(added) == 14
    assert any(isinstance(e, ActiveZoneSensor) for e in added)
    assert any(isinstance(e, LastWateringZoneSensor) for e in added)
