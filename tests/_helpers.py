"""Shared construction helpers for entity-level tests."""
from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense.api import IrrisenseApi
from custom_components.aiper_irrisense.const import DOMAIN
from custom_components.aiper_irrisense.coordinator import IrrisenseCoordinator


def make_coordinator(hass, data: dict[str, Any] | None = None) -> IrrisenseCoordinator:
    """Build a coordinator wired to `hass` with seeded data.

    Entity properties read `coordinator.data`; `active_zone_state` reads the
    internal `coordinator._data`. We point both at the same dict so seeded
    fixtures drive every code path.
    """
    api = IrrisenseApi("u", "p", "eu")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "region": "eu"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = IrrisenseCoordinator(hass, api, entry)
    data = data or {}
    coord.data = data
    coord._data = data
    coord.last_update_success = True
    return coord
