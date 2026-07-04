"""Unit tests for the IrrisenseApi MQTT reconnection helpers.

These exercise the pure-Python recovery logic (no real AWS IoT socket): the
last-received clock and the forced teardown + reconnect used by the
coordinator's health watchdog.
"""
from __future__ import annotations

import time

import pytest

from custom_components.aiper_irrisense import api as api_module
from custom_components.aiper_irrisense.api import IrrisenseApi


@pytest.fixture
def api() -> IrrisenseApi:
    return IrrisenseApi("user@example.com", "secret", "eu")


class FakeClient:
    """Stand-in for AWSIoTMQTTClient — records teardown calls."""

    def __init__(self) -> None:
        self.disconnected = False
        self.timeout = None

    def configureConnectDisconnectTimeout(self, seconds):  # noqa: N802 - SDK name
        self.timeout = seconds

    def disconnect(self):
        self.disconnected = True


def test_seconds_since_last_rx_none_before_first_frame(api: IrrisenseApi) -> None:
    assert api.seconds_since_last_rx() is None


def test_seconds_since_last_rx_after_frame(api: IrrisenseApi) -> None:
    api._last_rx_ts = time.time() - 5
    idle = api.seconds_since_last_rx()
    assert idle is not None
    assert 4.0 < idle < 60.0


def test_reconnect_noop_when_already_reconnecting(api: IrrisenseApi) -> None:
    api._reconnecting = True
    assert api.reconnect_mqtt() is False


def test_reconnect_tears_down_old_client_and_reconnects(
    api: IrrisenseApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Skip the 0.5s straggler-death settle sleep.
    monkeypatch.setattr(api_module.time, "sleep", lambda _s: None)

    old = FakeClient()
    api._mqtt_client = old
    api._mqtt_connected = True

    connect_calls = []

    def fake_connect() -> bool:
        connect_calls.append(True)
        return True

    monkeypatch.setattr(api, "connect_mqtt", fake_connect)

    result = api.reconnect_mqtt()

    assert result is True
    assert old.disconnected is True          # old client torn down
    assert old.timeout == 5                   # disconnect timeout trimmed
    assert len(connect_calls) == 1            # fresh connect attempted
    # State reset in the finally block.
    assert api._reconnecting is False
    assert api._expected_paho_deaths == 0
    assert api._mqtt_connected is False        # cleared before reconnect


def test_reconnect_propagates_connect_failure(
    api: IrrisenseApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_module.time, "sleep", lambda _s: None)
    api._mqtt_client = FakeClient()
    monkeypatch.setattr(api, "connect_mqtt", lambda: False)

    assert api.reconnect_mqtt() is False
    assert api._reconnecting is False  # still reset even on failure
