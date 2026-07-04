"""Unit tests for IrrisenseApi pure helpers and command-payload building."""
from __future__ import annotations

import time

import pytest

from custom_components.aiper_irrisense.api import IrrisenseApi, _find_map_url
from custom_components.aiper_irrisense.const import (
    CMD_SET_WORK_MODE,
    CMD_WORK_INFO,
    REGION_TYPE_AREA,
    REGION_TYPE_POINT,
    STATUS_RUNNING,
    STATUS_STOPPED,
    WATER_YIELD_LOW,
)


@pytest.fixture
def api() -> IrrisenseApi:
    return IrrisenseApi("user@example.com", "secret", "eu")


# --------------------------------------------------------------------------- #
# _is_success
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"code": 0}, True),
        ({"code": "0"}, True),
        ({"code": 200}, True),
        ({"code": "200"}, True),
        ({"successful": True}, True),
        ({"code": 6002}, False),
        ({"code": "401"}, False),
        ({}, False),
        ("not-a-dict", False),
    ],
)
def test_is_success(payload, expected) -> None:
    assert IrrisenseApi._is_success(payload) is expected


# --------------------------------------------------------------------------- #
# _parse_regions
# --------------------------------------------------------------------------- #
def test_parse_regions_slims_and_defaults() -> None:
    zmap = {
        "regions": [
            {
                "id": 1,
                "name": "Lawn",
                "type": 0,
                "sort": 2,
                "waterYield": 0.25,
                "pointTime": 5,
                "points": [{"x": 1}, {"x": 2}, {"x": 3}],
                "usageStatus": 1,
            },
            {"id": 2},  # everything else defaulted
        ]
    }
    regions = IrrisenseApi._parse_regions(zmap)
    assert len(regions) == 2

    first = regions[0]
    assert first["id"] == 1
    assert first["name"] == "Lawn"
    assert first["type"] == 0
    assert first["sort"] == 2
    assert first["waterYield"] == 0.25
    assert first["pointTime"] == 5
    assert first["n_points"] == 3
    assert first["usageStatus"] == 1

    second = regions[1]
    assert second["name"] == "Zone 2"          # synthesized default name
    assert second["type"] == 0
    assert second["waterYield"] == WATER_YIELD_LOW
    assert second["pointTime"] == 1
    assert second["n_points"] == 0


@pytest.mark.parametrize(
    "zmap",
    [None, "nope", {}, {"regions": "notlist"}],
)
def test_parse_regions_bad_input_returns_empty(zmap) -> None:
    assert IrrisenseApi._parse_regions(zmap) == []


def test_parse_regions_skips_invalid_entries() -> None:
    zmap = {"regions": ["str", {"no": "id"}, {"id": "notint"}, {"id": 7}]}
    regions = IrrisenseApi._parse_regions(zmap)
    assert [r["id"] for r in regions] == [7]


# --------------------------------------------------------------------------- #
# _find_map_url
# --------------------------------------------------------------------------- #
def test_find_map_url_prefers_url_keys() -> None:
    obj = {"data": {"mapUrl": "https://example.com/map.json", "other": "x"}}
    assert _find_map_url(obj) == "https://example.com/map.json"


def test_find_map_url_scans_nested_list() -> None:
    obj = {"items": [{"a": 1}, {"deep": {"link": "http://host/f"}}]}
    assert _find_map_url(obj) == "http://host/f"


def test_find_map_url_none_when_absent() -> None:
    assert _find_map_url({"a": 1, "b": [2, 3]}) is None
    assert _find_map_url("plain string") is None


# --------------------------------------------------------------------------- #
# note_upchan_ack
# --------------------------------------------------------------------------- #
def test_note_upchan_ack_clears_pending(api: IrrisenseApi) -> None:
    api._pending_ack[("SN1", "setWorkMode")] = time.time()
    api.note_upchan_ack("SN1", "setWorkMode")
    assert ("SN1", "setWorkMode") not in api._pending_ack


def test_note_upchan_ack_unknown_is_noop(api: IrrisenseApi) -> None:
    api.note_upchan_ack("SN1", "never-armed")  # must not raise
    assert api._pending_ack == {}


# --------------------------------------------------------------------------- #
# start_zone / stop_zone / query_work_info payload building
# --------------------------------------------------------------------------- #
@pytest.fixture
def capture_publish(api: IrrisenseApi, monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, dict]] = []

    def _fake(sn, cmd_type, data):
        calls.append((sn, cmd_type, data))
        return True

    monkeypatch.setattr(api, "_publish_cmd", _fake)
    return calls


def test_start_zone_area_sends_water_yield(api, capture_publish) -> None:
    assert api.start_zone("SN", 1, region_type=REGION_TYPE_AREA, water_yield=0.25)
    sn, cmd, body = capture_publish[0]
    assert (sn, cmd) == ("SN", CMD_SET_WORK_MODE)
    assert body["map_id"] == 1
    assert body["status"] == STATUS_RUNNING
    assert body["waterYield"] == 0.25
    assert "point_time" not in body


def test_start_zone_point_sends_point_time(api, capture_publish) -> None:
    assert api.start_zone("SN", 3, region_type=REGION_TYPE_POINT, point_time=10)
    _sn, _cmd, body = capture_publish[0]
    assert body["point_time"] == 10
    assert "waterYield" not in body


def test_start_zone_pesticide_area_attaches_pesticide_fields(api, capture_publish) -> None:
    assert api.start_zone(
        "SN",
        1,
        region_type=REGION_TYPE_AREA,
        pesticide=True,
        pesticides_sn="PST-1",
        used_amount=2.5,
    )
    _sn, _cmd, body = capture_publish[0]
    assert body["mode"] == 1                     # MODE_PESTICIDE
    assert body["waterYield"] == WATER_YIELD_LOW
    assert body["pesticides_sn"] == "PST-1"
    assert body["used_amount"] == 2.5
    assert "point_time" not in body


def test_stop_zone_payload(api, capture_publish) -> None:
    assert api.stop_zone("SN", 4)
    sn, cmd, body = capture_publish[0]
    assert (sn, cmd) == ("SN", CMD_SET_WORK_MODE)
    assert body == {"mode": 0, "map_id": 4, "status": STATUS_STOPPED}


def test_query_work_info_payload(api, capture_publish) -> None:
    assert api.query_work_info("SN")
    sn, cmd, body = capture_publish[0]
    assert (sn, cmd) == ("SN", CMD_WORK_INFO)
    assert body == {}


# --------------------------------------------------------------------------- #
# ACK watchdog timeout branch
# --------------------------------------------------------------------------- #
def test_ack_watchdog_times_out_and_clears(api: IrrisenseApi) -> None:
    api._ack_timeout = 0.05
    api._pending_ack[("SN", "setWorkMode")] = time.time()
    api._schedule_ack_watchdog("SN", "setWorkMode", "{}")
    time.sleep(0.2)
    # Watchdog fired: it pops the still-pending entry (logging a warning).
    assert ("SN", "setWorkMode") not in api._pending_ack
