"""Unit tests for the paho-mqtt reconnection layer.

These exercise the pure-Python recovery logic (no real broker / paho install):
the last-received clock, the SigV4 path builder, the paho callbacks, and the
reconnect supervisor that re-signs a fresh URL on every attempt.
"""
from __future__ import annotations

import time

import pytest

from custom_components.aiper_irrisense import api as api_module
from custom_components.aiper_irrisense.api import IrrisenseApi


@pytest.fixture
def api() -> IrrisenseApi:
    a = IrrisenseApi("user@example.com", "secret", "eu")
    a._iot_endpoint = "abc123-ats.iot.eu-central-1.amazonaws.com"
    a._aws_region = "eu-central-1"
    return a


CREDS = {"AccessKeyId": "AKIA", "SecretKey": "sk", "SessionToken": "tok"}


class FakeClient:
    """Minimal stand-in for a paho ``mqtt.Client``."""

    def __init__(self) -> None:
        self.ws_paths: list[str] = []
        self.reconnect_calls = 0
        self.disconnected = False
        self.loop_stopped = False

    def ws_set_options(self, path=None):
        self.ws_paths.append(path)

    def reconnect(self):
        self.reconnect_calls += 1

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        self.loop_stopped = True


# --------------------------------------------------------------------------- #
# seconds_since_last_rx
# --------------------------------------------------------------------------- #
def test_seconds_since_last_rx_none_before_first_frame(api: IrrisenseApi) -> None:
    assert api.seconds_since_last_rx() is None


def test_seconds_since_last_rx_after_frame(api: IrrisenseApi) -> None:
    api._last_rx_ts = time.time() - 5
    idle = api.seconds_since_last_rx()
    assert idle is not None and 4.0 < idle < 60.0


# --------------------------------------------------------------------------- #
# SigV4 path + region
# --------------------------------------------------------------------------- #
def test_aws_iot_region_from_endpoint() -> None:
    a = IrrisenseApi("u", "p", "eu")
    a._aws_region = None
    a._iot_endpoint = "xyz-ats.iot.us-east-1.amazonaws.com"
    assert a._aws_iot_region() == "us-east-1"


def test_build_ws_path_is_signed_mqtt_path(api: IrrisenseApi) -> None:
    path = api._build_ws_path(CREDS)
    assert path.startswith("/mqtt?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in path
    assert "X-Amz-Signature=" in path
    # Session token is appended AFTER signing.
    assert "X-Amz-Security-Token=tok" in path


# --------------------------------------------------------------------------- #
# _rc_is_success
# --------------------------------------------------------------------------- #
class _RC:
    def __init__(self, is_failure): self.is_failure = is_failure


@pytest.mark.parametrize(
    ("rc", "expected"),
    [
        (None, True),
        (0, True),
        (5, False),
        (_RC(False), True),
        (_RC(True), False),
    ],
)
def test_rc_is_success(rc, expected) -> None:
    assert IrrisenseApi._rc_is_success(rc) is expected


# --------------------------------------------------------------------------- #
# paho callbacks
# --------------------------------------------------------------------------- #
def test_on_connect_success_seeds_and_replays(api: IrrisenseApi, monkeypatch) -> None:
    replayed: list[str] = []
    monkeypatch.setattr(api, "subscribe_device", lambda sn, cb: replayed.append(sn))
    cb = lambda sn, data: None  # noqa: E731
    api._subscribed = {"WRX1": cb, "WRZ2": cb}
    api._last_rx_ts = 0.0

    api._on_connect(None, None, None, 0)

    assert api._mqtt_connected is True
    assert api._last_rx_ts > 0.0
    assert set(replayed) == {"WRX1", "WRZ2"}


def test_on_connect_refused_does_not_mark_connected(api: IrrisenseApi) -> None:
    api._on_connect(None, None, None, 5)
    assert api._mqtt_connected is False


def test_on_disconnect_schedules_reconnect(api: IrrisenseApi, monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(api, "_schedule_reconnect", lambda: scheduled.append(True))
    api._mqtt_connected = True
    api._on_disconnect(None, None, 7)  # rc=7, unintentional
    assert api._mqtt_connected is False
    assert scheduled == [True]


def test_on_disconnect_intentional_is_quiet(api: IrrisenseApi, monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(api, "_schedule_reconnect", lambda: scheduled.append(True))
    api._intentional_disconnect = True
    api._on_disconnect(None, None, 0)
    assert scheduled == []


# --------------------------------------------------------------------------- #
# reconnect_mqtt (watchdog entry point)
# --------------------------------------------------------------------------- #
def test_reconnect_mqtt_no_client_calls_connect(api: IrrisenseApi, monkeypatch) -> None:
    called = []
    api._mqtt_client = None
    monkeypatch.setattr(api, "connect_mqtt", lambda: called.append(True) or True)
    assert api.reconnect_mqtt() is True
    assert called == [True]


def test_reconnect_mqtt_with_client_schedules(api: IrrisenseApi, monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(api, "_schedule_reconnect", lambda: scheduled.append(True))
    api._mqtt_client = FakeClient()
    api._mqtt_connected = True
    assert api.reconnect_mqtt() is True
    assert api._mqtt_connected is False   # dropped so the supervisor loop runs
    assert scheduled == [True]


# --------------------------------------------------------------------------- #
# _schedule_reconnect supervisor: re-signs a fresh URL then reconnect()s
# --------------------------------------------------------------------------- #
def test_schedule_reconnect_resigns_and_reconnects(api: IrrisenseApi, monkeypatch) -> None:
    import threading

    # No-op sleep so the supervisor runs instantly; we join the thread to wait.
    monkeypatch.setattr(api_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(api, "_get_aws_credentials", lambda: CREDS)
    monkeypatch.setattr(api, "_build_ws_path", lambda creds: "/mqtt?fresh")

    client = FakeClient()
    # Simulate the broker accepting: reconnect() → _on_connect flips the flag.
    def _reconnect():
        client.reconnect_calls += 1
        api._mqtt_connected = True
    client.reconnect = _reconnect
    api._mqtt_client = client
    api._mqtt_connected = False

    before = set(threading.enumerate())
    api._schedule_reconnect()
    for t in threading.enumerate():
        if t not in before and t.name == "irrisense-mqtt-reconnect":
            t.join(timeout=3)

    assert client.ws_paths == ["/mqtt?fresh"]   # re-signed once
    assert client.reconnect_calls == 1
    assert api._reconnecting is False


def test_schedule_reconnect_single_supervisor(api: IrrisenseApi) -> None:
    # If one is already running, a second call is a no-op.
    api._reconnecting = True
    api._schedule_reconnect()   # must not raise / spawn
    assert api._reconnecting is True
