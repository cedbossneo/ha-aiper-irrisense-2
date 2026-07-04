"""Tests for the select platform: Nozzle type, Zone, and Dose selects.

These exercise the option lists, current_option resolution, and the
async_select_option write paths (both the pure selection-state selects and
the Nozzle select which persists to the server via the coordinator)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense.api import IrrisenseApi
from custom_components.aiper_irrisense.const import DOMAIN
from custom_components.aiper_irrisense.coordinator import IrrisenseCoordinator
from custom_components.aiper_irrisense.select import (
    DoseSelect,
    NozzleTypeSelect,
    ZoneSelect,
    _label_for_region,
    async_setup_entry,
)

SN = "SN"

# A two-zone map: an Area zone (type 0) and a Point zone (type 2).
MAP_DATA = {
    SN: {
        "map": {
            "regions": [
                {"id": 1, "name": "Lawn", "type": 0},
                {"id": 2, "name": "Bed", "type": 2},
            ]
        }
    }
}


def make_coordinator(hass, data) -> IrrisenseCoordinator:
    api = IrrisenseApi("u", "p", "eu")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "region": "eu"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = IrrisenseCoordinator(hass, api, entry)
    coord.data = data
    coord._data = data
    coord.last_update_success = True
    coord._test_entry = entry
    return coord


def _bind(entity, hass):
    """Attach hass and neutralise state writes so async_select_option runs
    without the entity being registered in the state machine."""
    entity.hass = hass
    entity.async_write_ha_state = lambda: None
    return entity


# --------------------------------------------------------------------------- #
# _label_for_region helper
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("region", "expected"),
    [
        ({"id": 1, "name": "Lawn", "type": 0}, "Lawn (Area)"),
        ({"id": 1, "name": "Row", "type": 1}, "Row (Line)"),
        ({"id": 1, "name": "Bed", "type": 2}, "Bed (Point)"),
        ({"id": 1, "name": "X", "type": 9}, "X (Zone)"),  # unknown type tag
        ({"id": 7}, "Zone 7 (Area)"),  # no name → synthesised
    ],
)
def test_label_for_region(region, expected) -> None:
    assert _label_for_region(region) == expected


# --------------------------------------------------------------------------- #
# NozzleTypeSelect
# --------------------------------------------------------------------------- #
def test_nozzle_options(hass) -> None:
    coord = make_coordinator(hass, {SN: {}})
    sel = NozzleTypeSelect(coord, SN)
    assert sel.options == ["Standard", "Jet"]
    assert sel.name == "Nozzle type"
    assert sel.available is True


@pytest.mark.parametrize(
    ("nozzle", "expected"),
    [
        ({"nozzleType": 0}, "Standard"),
        ({"nozzleType": 1}, "Standard"),
        ({"nozzleType": 2}, "Jet"),
        ({"nozzle_type": "2"}, "Jet"),  # string digit coercion
        ({"type": "1"}, "Standard"),
        ({"nozzleType": 9}, None),  # unknown server code
        ({"nozzleType": "abc"}, None),  # non-numeric string ignored
        ({}, None),  # dict but no recognised key
        (None, None),  # nozzle slot missing / not a dict
    ],
)
def test_nozzle_current_option(hass, nozzle, expected) -> None:
    slot = {} if nozzle is None else {"nozzle": nozzle}
    coord = make_coordinator(hass, {SN: slot})
    sel = NozzleTypeSelect(coord, SN)
    assert sel.current_option == expected


async def test_nozzle_select_option_calls_coordinator(hass) -> None:
    coord = make_coordinator(hass, {SN: {}})
    coord.async_set_nozzle_type = AsyncMock(return_value=True)
    sel = NozzleTypeSelect(coord, SN)

    await sel.async_select_option("Jet")
    coord.async_set_nozzle_type.assert_awaited_once_with(SN, 1)

    coord.async_set_nozzle_type.reset_mock()
    await sel.async_select_option("Standard")
    coord.async_set_nozzle_type.assert_awaited_once_with(SN, 0)


async def test_nozzle_select_option_ignores_unknown(hass) -> None:
    coord = make_coordinator(hass, {SN: {}})
    coord.async_set_nozzle_type = AsyncMock(return_value=True)
    sel = NozzleTypeSelect(coord, SN)
    await sel.async_select_option("Bogus")
    coord.async_set_nozzle_type.assert_not_called()


# --------------------------------------------------------------------------- #
# ZoneSelect
# --------------------------------------------------------------------------- #
def test_zone_options_and_current(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = ZoneSelect(coord, SN)
    assert sel.options == ["Lawn (Area)", "Bed (Point)"]
    assert sel.name == "Watering zone"
    # No explicit pick → coordinator falls back to first zone.
    assert sel.current_option == "Lawn (Area)"


def test_zone_current_option_none_when_empty(hass) -> None:
    coord = make_coordinator(hass, {SN: {"map": {"regions": []}}})
    sel = ZoneSelect(coord, SN)
    assert sel.options == []
    assert sel.current_option is None


async def test_zone_select_option_updates_state(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = _bind(ZoneSelect(coord, SN), hass)

    await sel.async_select_option("Bed (Point)")
    assert coord.get_zone_selection(SN) == 2
    assert sel.current_option == "Bed (Point)"
    # Picking a Point zone reset dose to the point default.
    assert coord.get_dose_selection(SN) == "1 min"


async def test_zone_select_option_unknown_label_noop(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = _bind(ZoneSelect(coord, SN), hass)
    before = coord.get_zone_selection(SN)
    await sel.async_select_option("Does Not Exist (Area)")
    assert coord.get_zone_selection(SN) == before


def test_zone_on_map_updated_refreshes_options(hass) -> None:
    data = {SN: {"map": {"regions": [{"id": 1, "name": "Lawn", "type": 0}]}}}
    coord = make_coordinator(hass, data)
    sel = _bind(ZoneSelect(coord, SN), hass)
    assert sel.options == ["Lawn (Area)"]

    # Simulate a map refresh adding a second zone.
    data[SN]["map"]["regions"].append({"id": 2, "name": "Bed", "type": 2})
    sel._on_map_updated(SN, data[SN]["map"]["regions"])
    assert sel.options == ["Lawn (Area)", "Bed (Point)"]

    # A signal for a different device is ignored.
    data[SN]["map"]["regions"].append({"id": 3, "name": "Row", "type": 1})
    sel._on_map_updated("OTHER", [])
    assert sel.options == ["Lawn (Area)", "Bed (Point)"]


def test_zone_helpers_return_none_for_unknown(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = ZoneSelect(coord, SN)
    assert sel._zone_id_for_label("nope") is None
    assert sel._label_for_zone_id(999) is None


# --------------------------------------------------------------------------- #
# DoseSelect
# --------------------------------------------------------------------------- #
def test_dose_area_shape(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)  # first zone (id 1) is Area
    sel = DoseSelect(coord, SN)
    assert sel.options == ["3 mm", "6 mm", "13 mm"]
    assert sel.name == "Dose"
    assert sel.icon == "mdi:water"
    # No dose picked yet → type default.
    assert sel.current_option == "3 mm"


def test_dose_point_shape(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    coord.set_zone_selection(SN, 2)  # select the Point zone
    sel = DoseSelect(coord, SN)
    assert sel.options == ["1 min", "5 min", "10 min"]
    assert sel.name == "Duration"
    assert sel.icon == "mdi:timer-outline"
    assert sel.current_option == "1 min"


async def test_dose_select_option_updates_state(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = _bind(DoseSelect(coord, SN), hass)
    await sel.async_select_option("6 mm")
    assert coord.get_dose_selection(SN) == "6 mm"
    assert sel.current_option == "6 mm"


async def test_dose_select_option_offlist_noop(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = _bind(DoseSelect(coord, SN), hass)
    # "5 min" is a Point option, not valid for the current Area shape.
    await sel.async_select_option("5 min")
    assert coord.get_dose_selection(SN) != "5 min"


def test_dose_current_option_falls_back_when_offlist(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    # Force a stored value that is not in the Area option set.
    coord.set_dose_selection(SN, "5 min")
    sel = DoseSelect(coord, SN)
    assert sel.current_option == "3 mm"  # default surfaced, no mutation


def test_dose_on_selection_changed_reshapes(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = _bind(DoseSelect(coord, SN), hass)
    assert sel.options == ["3 mm", "6 mm", "13 mm"]

    # Switch selection to the Point zone, then fire the callback.
    coord.set_zone_selection(SN, 2)
    sel._on_selection_changed(SN)
    assert sel.options == ["1 min", "5 min", "10 min"]
    assert sel.name == "Duration"

    # A signal for a different device is ignored.
    coord.set_zone_selection(SN, 1)
    sel._on_selection_changed("OTHER")
    assert sel.options == ["1 min", "5 min", "10 min"]


def test_dose_on_map_updated_reshapes(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = _bind(DoseSelect(coord, SN), hass)
    coord.set_zone_selection(SN, 2)  # Point
    sel._on_map_updated(SN, [])
    assert sel.name == "Duration"
    # Different device ignored.
    coord.set_zone_selection(SN, 1)
    sel._on_map_updated("OTHER", [])
    assert sel.name == "Duration"


# --------------------------------------------------------------------------- #
# async_added_to_hass lifecycle (restore + dispatcher wiring)
# --------------------------------------------------------------------------- #
async def _added_to_hass(entity, hass, last_state):
    """Drive async_added_to_hass with the restore + dispatcher deps stubbed."""
    entity.hass = hass
    entity.entity_id = "select.test"
    entity.async_on_remove = lambda func: None
    entity.async_get_last_state = AsyncMock(return_value=last_state)
    with patch.object(
        type(entity).__mro__[2], "async_added_to_hass", AsyncMock()
    ), patch(
        "custom_components.aiper_irrisense.select.async_dispatcher_connect",
        return_value=lambda: None,
    ):
        await entity.async_added_to_hass()


async def test_zone_added_to_hass_restores_selection(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = ZoneSelect(coord, SN)
    await _added_to_hass(sel, hass, State("select.test", "Bed (Point)"))
    assert coord.get_zone_selection(SN) == 2


async def test_zone_added_to_hass_ignores_unknown_state(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = ZoneSelect(coord, SN)
    await _added_to_hass(sel, hass, State("select.test", "unavailable"))
    # No valid restore → coordinator still defaults to first zone.
    assert coord.get_zone_selection(SN) == 1


async def test_dose_added_to_hass_restores_selection(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = DoseSelect(coord, SN)
    await _added_to_hass(sel, hass, State("select.test", "6 mm"))
    assert coord.get_dose_selection(SN) == "6 mm"


async def test_dose_added_to_hass_ignores_unknown_state(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    sel = DoseSelect(coord, SN)
    await _added_to_hass(sel, hass, State("select.test", "unknown"))
    assert coord.get_dose_selection(SN) is None


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #
async def test_async_setup_entry_creates_three_per_device(hass) -> None:
    coord = make_coordinator(hass, MAP_DATA)
    # devices reads from the api cache; one valid device + one without an sn.
    coord.api._devices = {SN: {"sn": SN}, "x": {}}
    entry = coord._test_entry
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coordinator": coord}

    added: list = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))

    assert len(added) == 3
    kinds = {type(e).__name__ for e in added}
    assert kinds == {"NozzleTypeSelect", "ZoneSelect", "DoseSelect"}
