"""Tests for IrrisenseApi auth, device discovery and MQTT crash-shield state.

All network / AWS boundaries are mocked, so these exercise pure control flow.
"""
from __future__ import annotations

import pytest

from custom_components.aiper_irrisense.api import IrrisenseApi


@pytest.fixture
def api() -> IrrisenseApi:
    return IrrisenseApi("user@example.com", "secret", "eu")


# --------------------------------------------------------------------------- #
# login
# --------------------------------------------------------------------------- #
def test_login_success_sets_token_and_base_url(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_get_openid_token", lambda: None)
    monkeypatch.setattr(
        api,
        "_call_encrypted",
        lambda *a, **k: {
            "code": 0,
            "data": {
                "token": "TKN",
                "serialNumber": "user-1",
                "tokenExpires": 123,
                "domain": ["https://eu.example.com/"],
            },
        },
    )
    assert api.login() is True
    assert api._token == "TKN"
    assert api._user_id == "user-1"
    assert api.base_url == "https://eu.example.com"
    assert api._session.headers["token"] == "TKN"


def test_login_failure_raises(api, monkeypatch) -> None:
    monkeypatch.setattr(
        api, "_call_encrypted", lambda *a, **k: {"code": 401, "msg": "bad creds"}
    )
    with pytest.raises(Exception, match="Login failed: bad creds"):
        api.login()


def test_login_missing_token_raises(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_get_openid_token", lambda: None)
    monkeypatch.setattr(
        api, "_call_encrypted", lambda *a, **k: {"code": 0, "data": {"domain": []}}
    )
    with pytest.raises(Exception, match="No token"):
        api.login()


# --------------------------------------------------------------------------- #
# refresh_token
# --------------------------------------------------------------------------- #
def test_refresh_token_success(api, monkeypatch) -> None:
    monkeypatch.setattr(
        api, "_call_encrypted", lambda *a, **k: {"code": 0, "data": {"token": "NEW"}}
    )
    assert api.refresh_token() is True
    assert api._token == "NEW"
    assert api._session.headers["token"] == "NEW"


def test_refresh_token_failure_returns_false(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_call_encrypted", lambda *a, **k: {"code": 401})
    assert api.refresh_token() is False


def test_refresh_token_swallows_exception(api, monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("network")

    monkeypatch.setattr(api, "_call_encrypted", _boom)
    assert api.refresh_token() is False


# --------------------------------------------------------------------------- #
# get_devices (Irrisense serial filter)
# --------------------------------------------------------------------------- #
def test_get_devices_filters_by_serial_prefix(api, monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_call_encrypted",
        lambda *a, **k: {
            "code": 0,
            "data": [
                {"sn": "WRX123", "name": "Irrisense A"},
                {"sn": "WGX999", "name": "Irrisense B"},
                {"sn": "WRZ777", "name": "Irrisense C"},
                {"sn": "HJ0000", "name": "A pool cleaner — ignored"},
                {"name": "no sn — skipped"},
            ],
        },
    )
    devices = api.get_devices()
    sns = {d["sn"] for d in devices}
    assert sns == {"WRX123", "WGX999", "WRZ777"}
    assert set(api._devices) == {"WRX123", "WGX999", "WRZ777"}


def test_get_devices_unwraps_dict_payload(api, monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "_call_encrypted",
        lambda *a, **k: {"code": 0, "data": {"list": [{"sn": "WRX1"}]}},
    )
    assert [d["sn"] for d in api.get_devices()] == ["WRX1"]


def test_get_devices_failure_returns_empty(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_call_encrypted", lambda *a, **k: {"code": 500})
    assert api.get_devices() == []
