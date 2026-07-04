"""Tests for the coordinator's MQTT health watchdog and startup retry.

The API is replaced by a lightweight fake that records calls, so these tests
verify the coordinator's *decisions* (when to reconnect, when to re-subscribe,
how startup retries behave) without any real AWS IoT socket.
"""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense import coordinator as coord_module
from custom_components.aiper_irrisense.const import (
    CONF_ENABLE_MQTT,
    DOMAIN,
    MQTT_IDLE_RECONNECT_SECONDS,
)
from custom_components.aiper_irrisense.coordinator import IrrisenseCoordinator


class FakeApi:
    """Records the MQTT lifecycle calls the coordinator makes."""

    def __init__(
        self,
        *,
        devices: list[dict] | None = None,
        connected: bool = True,
        idle: float | None = 0.0,
    ) -> None:
        self._devices = {d["sn"]: d for d in (devices or [])}
        self._connected = connected
        self._idle = idle

        self.reconnect_calls = 0
        self.reconnect_result = True
        self.connect_calls = 0
        self.connect_results: list[bool] | None = None
        self.subscribed: list[str] = []
        self.queried: list[str] = []
        self.shadow_requested: list[str] = []

    # --- health signals -------------------------------------------------
    def is_mqtt_connected(self) -> bool:
        return self._connected

    def seconds_since_last_rx(self):
        return self._idle

    # --- recovery -------------------------------------------------------
    def reconnect_mqtt(self) -> bool:
        self.reconnect_calls += 1
        return self.reconnect_result

    def connect_mqtt(self) -> bool:
        self.connect_calls += 1
        if self.connect_results is not None:
            return self.connect_results.pop(0)
        return True

    # --- subscribe / nudge ---------------------------------------------
    def subscribe_device(self, sn: str, callback) -> bool:  # noqa: ANN001
        self.subscribed.append(sn)
        return True

    def query_work_info(self, sn: str) -> bool:
        self.queried.append(sn)
        return True

    def request_shadow(self, sn: str) -> bool:
        self.shadow_requested.append(sn)
        return True


def _make_coordinator(hass, api: FakeApi, *, mqtt_enabled: bool = True):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "region": "eu"},
        options={CONF_ENABLE_MQTT: mqtt_enabled},
    )
    entry.add_to_hass(hass)
    return IrrisenseCoordinator(hass, api, entry)


DEVICES = [{"sn": "WRX123"}, {"sn": "WRZ999"}]


async def test_health_check_noop_when_healthy(hass) -> None:
    api = FakeApi(devices=DEVICES, connected=True, idle=10.0)
    coord = _make_coordinator(hass, api)

    await coord._check_mqtt_health()

    assert api.reconnect_calls == 0
    assert api.subscribed == []


async def test_health_check_reconnects_when_down(hass) -> None:
    api = FakeApi(devices=DEVICES, connected=False)
    coord = _make_coordinator(hass, api)

    await coord._check_mqtt_health()

    assert api.reconnect_calls == 1
    # Re-subscribed + nudged every device after recovery.
    assert set(api.subscribed) == {"WRX123", "WRZ999"}
    assert set(api.queried) == {"WRX123", "WRZ999"}
    assert set(api.shadow_requested) == {"WRX123", "WRZ999"}


async def test_health_check_reconnects_when_idle_too_long(hass) -> None:
    api = FakeApi(
        devices=DEVICES,
        connected=True,
        idle=MQTT_IDLE_RECONNECT_SECONDS + 1,
    )
    coord = _make_coordinator(hass, api)

    await coord._check_mqtt_health()

    assert api.reconnect_calls == 1
    assert set(api.subscribed) == {"WRX123", "WRZ999"}


async def test_health_check_no_resubscribe_when_reconnect_fails(hass) -> None:
    api = FakeApi(devices=DEVICES, connected=False)
    api.reconnect_result = False
    coord = _make_coordinator(hass, api)

    await coord._check_mqtt_health()

    assert api.reconnect_calls == 1
    assert api.subscribed == []  # no point subscribing on a dead link


async def test_health_check_skipped_when_mqtt_disabled(hass) -> None:
    api = FakeApi(devices=DEVICES, connected=False)
    coord = _make_coordinator(hass, api, mqtt_enabled=False)

    await coord._check_mqtt_health()

    assert api.reconnect_calls == 0


async def test_health_check_skipped_when_no_devices(hass) -> None:
    api = FakeApi(devices=[], connected=False)
    coord = _make_coordinator(hass, api)

    await coord._check_mqtt_health()

    assert api.reconnect_calls == 0


async def test_start_mqtt_subscribes_on_first_success(hass) -> None:
    api = FakeApi(devices=DEVICES)
    coord = _make_coordinator(hass, api)

    await coord.async_start_mqtt(retry=True)

    assert api.connect_calls == 1
    assert set(api.subscribed) == {"WRX123", "WRZ999"}


async def test_start_mqtt_retries_until_success(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Don't actually wait between retries.
    monkeypatch.setattr(coord_module, "MQTT_SETUP_RETRY_SECONDS", 0)

    api = FakeApi(devices=DEVICES)
    api.connect_results = [False, False, True]
    coord = _make_coordinator(hass, api)

    await coord.async_start_mqtt(retry=True)

    assert api.connect_calls == 3
    assert set(api.subscribed) == {"WRX123", "WRZ999"}


async def test_start_mqtt_gives_up_after_max_attempts(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coord_module, "MQTT_SETUP_RETRY_SECONDS", 0)
    monkeypatch.setattr(coord_module, "MQTT_SETUP_MAX_ATTEMPTS", 3)

    api = FakeApi(devices=DEVICES)
    api.connect_results = [False, False, False]
    coord = _make_coordinator(hass, api)

    await coord.async_start_mqtt(retry=True)

    assert api.connect_calls == 3
    assert api.subscribed == []  # never connected, so never subscribed
