"""Tests for zone deletion: mapId capture, api call, coordinator wrapper."""
from __future__ import annotations

import pytest

from custom_components.aiper_irrisense.api import IrrisenseApi

from ._helpers import make_coordinator


# --------------------------------------------------------------------------- #
# api._map_id_from_list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"data": [{"id": 42, "mapUrl": "x"}]}, 42),
        ({"data": [{"id": "7"}]}, 7),
        ({"data": {"id": 5}}, 5),
        ({"data": [{"mapId": 9}]}, 9),
        ({"data": []}, None),
        ({"data": [{"nope": 1}]}, None),
        ("bad", None),
    ],
)
def test_map_id_from_list(info, expected) -> None:
    assert IrrisenseApi._map_id_from_list(info) == expected


def test_delete_map_region_posts_expected_body(monkeypatch) -> None:
    api = IrrisenseApi("u", "p", "eu")
    captured: dict = {}

    def _fake_write(path, body):
        captured["path"] = path
        captured["body"] = body
        return True

    monkeypatch.setattr(api, "_wr_write", _fake_write)
    assert api.delete_map_region("SN", 42, [2, 3]) is True
    assert captured["path"] == "/wr/deleteMapRegion"
    assert captured["body"] == {"sn": "SN", "mapId": 42, "regionIdList": [2, 3]}


def test_map_id_for_returns_cached(monkeypatch) -> None:
    api = IrrisenseApi("u", "p", "eu")
    assert api.map_id_for("SN") is None
    api._map_id_by_sn["SN"] = 11
    assert api.map_id_for("SN") == 11


# --------------------------------------------------------------------------- #
# coordinator.async_delete_zone
# --------------------------------------------------------------------------- #
async def test_async_delete_zone_success(hass, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    coord = make_coordinator(hass, {"SN": {}})
    coord.api._map_id_by_sn["SN"] = 42
    coord.async_request_refresh = AsyncMock()   # don't hit the network
    calls: list = []
    monkeypatch.setattr(coord.api, "delete_map_region",
                        lambda sn, mid, ids: calls.append((sn, mid, ids)) or True)

    ok = await coord.async_delete_zone("SN", 3)
    assert ok is True
    assert calls == [("SN", 42, [3])]
    # Map cache invalidated so the next poll drops the zone.
    assert "SN" not in coord._last_map_fetch


async def test_async_delete_zone_without_map_id_is_noop(hass, monkeypatch) -> None:
    coord = make_coordinator(hass, {"SN": {}})
    called = []
    monkeypatch.setattr(coord.api, "delete_map_region",
                        lambda *a: called.append(a) or True)

    ok = await coord.async_delete_zone("SN", 3)
    assert ok is False          # mapId unknown → refuse
    assert called == []
