"""Tests for the diagnostics dump + slot summarizer."""
from __future__ import annotations

from custom_components.aiper_irrisense.const import DOMAIN
from custom_components.aiper_irrisense.diagnostics import (
    _summarize_device_slot,
    async_get_config_entry_diagnostics,
)

from ._helpers import make_coordinator

REDACTED = "**REDACTED**"


def test_summarize_device_slot_keeps_and_trims() -> None:
    slot = {
        "equipment": {"sn": "SN", "online": 1},
        "wr_info": {"a": 1},
        "setting": {"rainSensing": 1},
        "nozzle": None,                       # dropped (None)
        "map": {
            "regions": [
                {"id": 1, "name": "Lawn", "type": 0, "points": [1, 2, 3]},
                "not-a-dict",                 # skipped
            ]
        },
        "mqtt": {"up_setWorkMode": {}, "cloud_report": {}},
        "history": {"huge": "dropped"},       # not in the keep-list
    }
    out = _summarize_device_slot(slot)

    assert out["equipment"] == {"sn": "SN", "online": 1}
    assert out["wr_info"] == {"a": 1}
    assert out["setting"] == {"rainSensing": 1}
    assert "nozzle" not in out               # None dropped
    assert "history" not in out              # not summarized
    assert out["map"]["regions"] == [
        {"id": 1, "name": "Lawn", "type": 0, "point_count": 3}
    ]
    assert out["mqtt_keys"] == ["cloud_report", "up_setWorkMode"]  # sorted


async def test_async_get_config_entry_diagnostics_redacts(hass) -> None:
    data = {
        "SN": {
            "equipment": {"sn": "SN", "online": 1, "token": "secret-token"},
            "mqtt": {"up_workInfo": {}},
        }
    }
    coord = make_coordinator(hass, data)
    entry = coord.entry

    # Wire the coordinator + api the way __init__ does.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coord,
        "api": coord.api,
    }

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Entry credentials redacted.
    assert diag["entry"]["data"]["username"] == REDACTED
    assert diag["entry"]["data"]["password"] == REDACTED
    # Real api is not MQTT-connected in a unit test.
    assert diag["mqtt_connected"] is False
    # Device slot summarized + token redacted inside equipment.
    dev = diag["devices"]["SN"]
    assert dev["equipment"]["token"] == REDACTED
    assert dev["mqtt_keys"] == ["up_workInfo"]


async def test_diagnostics_handles_missing_coordinator(hass) -> None:
    """No coordinator/api in hass.data → empty-but-valid dump."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["mqtt_connected"] is False
    assert diag["devices"] == {}
