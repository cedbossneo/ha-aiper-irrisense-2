"""Tests for the Start / Stop watering buttons."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.aiper_irrisense.button import (
    StartWateringButton,
    StopWateringButton,
)

from ._helpers import make_coordinator

AREA = {"id": 1, "name": "Lawn", "type": 0, "waterYield": 0.1, "pointTime": 1, "n_points": 0}
POINT = {"id": 2, "name": "Bed", "type": 2, "waterYield": 0.1, "pointTime": 5, "n_points": 3}


def _coord_with_zones(hass):
    data = {"SN": {"map": {"regions": [AREA, POINT]}}}
    coord = make_coordinator(hass, data)
    coord.async_start_zone = AsyncMock(return_value=True)
    coord.async_stop_zone = AsyncMock(return_value=True)
    return coord


# --------------------------------------------------------------------------- #
# Start button
# --------------------------------------------------------------------------- #
async def test_start_area_zone_sends_water_yield(hass) -> None:
    coord = _coord_with_zones(hass)
    coord.set_zone_selection("SN", 1)
    coord.set_dose_selection("SN", "6 mm")

    await StartWateringButton(coord, "SN").async_press()

    coord.async_start_zone.assert_awaited_once()
    _args, kwargs = coord.async_start_zone.call_args
    assert kwargs["water_yield"] == 0.25   # "6 mm" preset
    assert kwargs["point_time"] is None


async def test_start_point_zone_sends_point_time(hass) -> None:
    coord = _coord_with_zones(hass)
    coord.set_zone_selection("SN", 2)     # resets dose to "1 min"
    coord.set_dose_selection("SN", "10 min")

    await StartWateringButton(coord, "SN").async_press()

    _args, kwargs = coord.async_start_zone.call_args
    assert kwargs["point_time"] == 10
    assert kwargs["water_yield"] is None


async def test_start_guardrail_point_zone_with_mm_dose(hass) -> None:
    """Point zone but a stale mm dose → send neither field (zone-map default)."""
    coord = _coord_with_zones(hass)
    coord._zone_selection["SN"] = 2       # point zone, bypass the auto dose reset
    coord.set_dose_selection("SN", "3 mm")  # wrong kind for a point zone

    await StartWateringButton(coord, "SN").async_press()

    _args, kwargs = coord.async_start_zone.call_args
    assert kwargs["water_yield"] is None
    assert kwargs["point_time"] is None


async def test_start_no_zone_selected_is_noop(hass) -> None:
    data = {"SN": {"map": {"regions": []}}}  # no zones → get_zone_selection None
    coord = make_coordinator(hass, data)
    coord.async_start_zone = AsyncMock()

    await StartWateringButton(coord, "SN").async_press()

    coord.async_start_zone.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Stop button
# --------------------------------------------------------------------------- #
async def test_stop_prefers_active_zone(hass) -> None:
    coord = _coord_with_zones(hass)
    coord.active_zone_state = lambda sn: {"is_running": True, "zone_id": 2}
    coord.set_zone_selection("SN", 1)     # selection differs from active

    await StopWateringButton(coord, "SN").async_press()

    coord.async_stop_zone.assert_awaited_once_with("SN", 2)


async def test_stop_falls_back_to_selection_when_idle(hass) -> None:
    coord = _coord_with_zones(hass)
    coord.active_zone_state = lambda sn: None
    coord.set_zone_selection("SN", 1)

    await StopWateringButton(coord, "SN").async_press()

    coord.async_stop_zone.assert_awaited_once_with("SN", 1)


async def test_stop_noop_when_nothing_active_or_selected(hass) -> None:
    data = {"SN": {"map": {"regions": []}}}
    coord = make_coordinator(hass, data)
    coord.async_stop_zone = AsyncMock()
    coord.active_zone_state = lambda sn: None

    await StopWateringButton(coord, "SN").async_press()

    coord.async_stop_zone.assert_not_awaited()
