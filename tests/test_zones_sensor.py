"""Tests for the dynamic ZonesSensor (drives the auto-entities dashboard)."""
from __future__ import annotations

from custom_components.aiper_irrisense.sensor import ZonesSensor

from ._helpers import make_coordinator

REGIONS = [
    {"id": 1, "name": "Lawn", "type": 0, "waterYield": 0.1, "pointTime": 1, "n_points": 0},
    {"id": 2, "name": "Bed", "type": 2, "waterYield": 0.1, "pointTime": 5, "n_points": 3},
    {"id": 3, "name": "", "type": 1, "waterYield": 0.5, "pointTime": 1, "n_points": 0},
]


def _coord(hass, regions=REGIONS, active=None):
    coord = make_coordinator(hass, {"SN": {"map": {"regions": regions}}})
    coord.active_zone_state = lambda sn: active
    return coord


def test_native_value_is_zone_count(hass) -> None:
    assert ZonesSensor(_coord(hass), "SN").native_value == 3


def test_native_value_zero_without_map(hass) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    coord.active_zone_state = lambda sn: None
    ent = ZonesSensor(coord, "SN")
    assert ent.native_value == 0
    assert ent.extra_state_attributes["zones"] == []


def test_zone_list_shape(hass) -> None:
    ent = ZonesSensor(_coord(hass), "SN")
    zones = ent.extra_state_attributes["zones"]
    assert len(zones) == 3

    lawn, bed, line = zones
    assert lawn["id"] == 1
    assert lawn["name"] == "Lawn"
    assert lawn["select_label"] == "Lawn (Area)"   # matches ZoneSelect option
    assert lawn["type_label"] == "Area"
    assert lawn["dose_unit"] == "mm"
    assert lawn["default_dose_label"] == "3 mm"   # waterYield 0.1
    assert lawn["is_running"] is False

    assert bed["type_label"] == "Point"
    assert bed["select_label"] == "Bed (Point)"
    assert bed["dose_unit"] == "min"
    assert bed["default_dose_label"] == "5 min"    # pointTime 5
    assert bed["n_points"] == 3

    # Unnamed zone gets a synthesized "Zone {id}" name.
    assert line["name"] == "Zone 3"
    assert line["select_label"] == "Zone 3 (Line)"
    assert line["type_label"] == "Line"
    assert line["default_dose_label"] == "13 mm"   # waterYield 0.5


def test_running_zone_flagged(hass) -> None:
    active = {"is_running": True, "zone_id": 2, "zone_name": "Bed"}
    ent = ZonesSensor(_coord(hass, active=active), "SN")
    attrs = ent.extra_state_attributes

    assert attrs["active_zone_id"] == 2
    assert attrs["active_zone_name"] == "Bed"
    running = {z["id"]: z["is_running"] for z in attrs["zones"]}
    assert running == {1: False, 2: True, 3: False}


def test_no_active_zone_when_idle(hass) -> None:
    ent = ZonesSensor(_coord(hass, active=None), "SN")
    attrs = ent.extra_state_attributes
    assert attrs["active_zone_id"] is None
    assert all(not z["is_running"] for z in attrs["zones"])
