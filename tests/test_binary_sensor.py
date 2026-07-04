"""Tests for the binary_sensor platform and the shared entity base."""
from __future__ import annotations

import time

import pytest

from custom_components.aiper_irrisense.binary_sensor import (
    OnlineBinarySensor,
    RainSensingBinarySensor,
    WateringBinarySensor,
)

from ._helpers import make_coordinator


# --------------------------------------------------------------------------- #
# OnlineBinarySensor
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("online", "expected"),
    [
        (1, True),
        ("1", True),
        ("online", True),
        (True, True),
        (0, False),
        ("0", False),
        (None, False),
        ("offline", False),
    ],
)
def test_online_binary_sensor(hass, online, expected) -> None:
    data = {"SN": {"equipment": {"sn": "SN", "online": online}}}
    coord = make_coordinator(hass, data)
    ent = OnlineBinarySensor(coord, "SN")
    assert ent.is_on is expected


# --------------------------------------------------------------------------- #
# WateringBinarySensor
# --------------------------------------------------------------------------- #
def test_watering_binary_sensor_running(hass) -> None:
    data = {
        "SN": {
            "map": {"regions": [{"id": 1, "name": "Lawn", "type": 0,
                                 "waterYield": 0.1, "pointTime": 1, "n_points": 0}]},
            "mqtt": {
                "up_realTimeProgress": {
                    "data": {"status": 1, "map_info": {"id": 1}, "time": 20, "progress": 8},
                    "_ts": time.time(),
                }
            },
        }
    }
    coord = make_coordinator(hass, data)
    assert WateringBinarySensor(coord, "SN").is_on is True


def test_watering_binary_sensor_idle(hass) -> None:
    data = {"SN": {"map": {"regions": []}, "mqtt": {}}}
    coord = make_coordinator(hass, data)
    assert WateringBinarySensor(coord, "SN").is_on is False


# --------------------------------------------------------------------------- #
# RainSensingBinarySensor
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ({"rainSensing": 1, "weatherSensingRain": 1}, True),
        ({"rainSensing": 1, "weatherSensingRain": 0}, False),
        ({"rainSensing": 0, "weatherSensingRain": 1}, False),
        ({}, False),
        (None, False),
    ],
)
def test_rain_sensing_binary_sensor(hass, setting, expected) -> None:
    data = {"SN": {"setting": setting}}
    coord = make_coordinator(hass, data)
    assert RainSensingBinarySensor(coord, "SN").is_on is expected


# --------------------------------------------------------------------------- #
# Shared entity base (device_info, available, data accessors)
# --------------------------------------------------------------------------- #
def test_entity_base_device_info_and_available(hass) -> None:
    data = {
        "SN": {
            "equipment": {
                "sn": "SN",
                "name": "Garden bot",
                "modelName": "WRX",
                "firmwareVersion": "3.9.4",
            }
        }
    }
    coord = make_coordinator(hass, data)
    ent = OnlineBinarySensor(coord, "SN")

    info = ent.device_info
    assert info["serial_number"] == "SN"
    assert info["name"] == "Garden bot"
    assert info["model"] == "WRX"
    assert info["sw_version"] == "3.9.4"
    assert ent.unique_id == "SN_online"
    assert ent.available is True

    coord.last_update_success = False
    assert ent.available is False


def test_entity_base_device_info_falls_back_to_api_cache(hass) -> None:
    coord = make_coordinator(hass, {})
    # No slot in coordinator.data → falls back to api._devices.
    coord.api._devices["SN"] = {"sn": "SN"}
    ent = OnlineBinarySensor(coord, "SN")
    info = ent.device_info
    assert info["model"] == "Irrisense 2"          # default model
    assert info["name"] == "Irrisense SN"          # default name
