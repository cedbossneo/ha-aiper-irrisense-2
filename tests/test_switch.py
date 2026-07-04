"""Tests for the switch platform.

Covers the schedule switch, the setting/reminder-backed switches, the
`_extract_task_list` normalizer, and `async_setup_entry` entity creation.
The coordinator/API write methods are replaced with AsyncMocks so no network
or MQTT is touched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense.api import IrrisenseApi
from custom_components.aiper_irrisense.const import DOMAIN
from custom_components.aiper_irrisense.coordinator import IrrisenseCoordinator
from custom_components.aiper_irrisense.switch import (
    DrainageReminderSwitch,
    PesticideReminderSwitch,
    RainSensingSwitch,
    ScheduleSwitch,
    TaskReminderSwitch,
    WaterShortageReminderSwitch,
    WindSensingSwitch,
    _extract_task_list,
    async_setup_entry,
)
from homeassistant.exceptions import HomeAssistantError


def make_coordinator(hass, data=None) -> IrrisenseCoordinator:
    api = IrrisenseApi("u", "p", "eu")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "region": "eu"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = IrrisenseCoordinator(hass, api, entry)
    if data is not None:
        coord.data = data
        coord._data = data
    coord.last_update_success = True
    return coord


# --------------------------------------------------------------------------- #
# _extract_task_list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("obj", "expected_ids"),
    [
        ([{"id": 1}, {"id": 2}, "junk"], [1, 2]),
        ({"list": [{"id": 3}]}, [3]),
        ({"records": [{"id": 4}, 5]}, [4]),
        ({"data": [{"id": 6}]}, [6]),
        ({"nothing": []}, []),
        ("not-a-container", []),
        (123, []),
    ],
)
def test_extract_task_list(obj, expected_ids) -> None:
    result = _extract_task_list(obj)
    assert [t.get("id") for t in result] == expected_ids


# --------------------------------------------------------------------------- #
# ScheduleSwitch
# --------------------------------------------------------------------------- #
def _task(**kw):
    base = {
        "id": 10,
        "planId": 7,
        "regionId": 1,
        "startTime": "06:30",
        "enabled": True,
        "repeatDays": "1,2,3",
        "estimatedDuration": 15,
        "depth": 5,
        "duration": 12,
    }
    base.update(kw)
    return base


def _schedule_data(task):
    return {"SN": {"tasks": [task], "map": {"regions": [{"id": 1, "name": "Lawn"}]}}}


def test_schedule_switch_name_with_zone(hass) -> None:
    task = _task()
    coord = make_coordinator(hass, _schedule_data(task))
    sw = ScheduleSwitch(coord, "SN", 10, task)
    assert "Plan 7" in sw._attr_name
    assert "Lawn" in sw._attr_name
    assert "@ 06:30" in sw._attr_name


def test_schedule_switch_name_without_zone_or_plan(hass) -> None:
    # No planId, and regionId has no matching zone name -> "Zone {id}".
    task = _task(planId=None, regionId=99, startTime=None)
    coord = make_coordinator(hass, _schedule_data(task))
    sw = ScheduleSwitch(coord, "SN", 10, task)
    assert "Plan" not in sw._attr_name
    assert "Zone 99" in sw._attr_name
    assert "@ —" in sw._attr_name


def test_schedule_switch_name_no_region(hass) -> None:
    task = _task(regionId=None)
    coord = make_coordinator(hass, _schedule_data(task))
    sw = ScheduleSwitch(coord, "SN", 10, task)
    assert "Zone" not in sw._attr_name


@pytest.mark.parametrize(
    ("val", "expected"),
    [
        ({"enabled": True}, True),
        ({"enabled": False}, False),
        ({"enabled": None, "isEnabled": 1}, True),
        ({"enabled": "true"}, True),
        ({"enabled": "0"}, False),
        ({"enabled": 3.5}, False),  # non-bool/int/str -> False
    ],
)
def test_schedule_switch_is_on(hass, val, expected) -> None:
    task = _task(**val)
    coord = make_coordinator(hass, _schedule_data(task))
    sw = ScheduleSwitch(coord, "SN", 10, task)
    assert sw.is_on is expected


def test_schedule_switch_is_on_missing_task(hass) -> None:
    # Task id 10 requested but data holds a different id -> _task() None -> False.
    coord = make_coordinator(hass, _schedule_data(_task(id=999)))
    sw = ScheduleSwitch(coord, "SN", 10, _task())
    assert sw._task() is None
    assert sw.is_on is False


def test_schedule_switch_task_lookup_via_taskid_and_bad_id(hass) -> None:
    task = {"taskId": 10, "enabled": True}
    data = {"SN": {"tasks": [{"id": "bad"}, {"taskId": None}, task]}}
    coord = make_coordinator(hass, data)
    sw = ScheduleSwitch(coord, "SN", 10, {"regionId": None})
    assert sw._task() == task


def test_schedule_switch_extra_state_attributes(hass) -> None:
    task = _task()
    coord = make_coordinator(hass, _schedule_data(task))
    sw = ScheduleSwitch(coord, "SN", 10, task)
    attrs = sw.extra_state_attributes
    assert attrs["plan_id"] == 7
    assert attrs["region_id"] == 1
    assert attrs["start_time"] == "06:30"
    assert attrs["repeat_days"] == "Mon,Tue,Wed"
    assert attrs["estimated_duration_min"] == 15
    assert attrs["depth_mm"] == 5
    assert attrs["duration_min"] == 12


def test_schedule_switch_extra_state_attributes_none_when_no_task(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"tasks": []}})
    sw = ScheduleSwitch(coord, "SN", 10, {"regionId": None})
    assert sw.extra_state_attributes is None


def test_schedule_switch_repeat_days_non_string_passthrough(hass) -> None:
    task = _task(repeatDays=[1, 2])  # not a string -> passthrough
    coord = make_coordinator(hass, _schedule_data(task))
    sw = ScheduleSwitch(coord, "SN", 10, task)
    assert sw.extra_state_attributes["repeat_days"] == [1, 2]


async def test_schedule_switch_turn_on_off(hass) -> None:
    task = _task()
    coord = make_coordinator(hass, _schedule_data(task))
    coord.async_set_schedule_enabled = AsyncMock(return_value=True)
    sw = ScheduleSwitch(coord, "SN", 10, task)

    await sw.async_turn_on()
    coord.async_set_schedule_enabled.assert_awaited_with("SN", task, True)

    await sw.async_turn_off()
    coord.async_set_schedule_enabled.assert_awaited_with("SN", task, False)


async def test_schedule_switch_turn_on_failure_raises(hass) -> None:
    task = _task()
    coord = make_coordinator(hass, _schedule_data(task))
    coord.async_set_schedule_enabled = AsyncMock(return_value=False)
    sw = ScheduleSwitch(coord, "SN", 10, task)
    with pytest.raises(HomeAssistantError):
        await sw.async_turn_on()
    with pytest.raises(HomeAssistantError):
        await sw.async_turn_off()


# --------------------------------------------------------------------------- #
# _SettingSwitch subclasses
# --------------------------------------------------------------------------- #
def test_setting_switch_is_on(hass) -> None:
    data = {"SN": {"setting": {"weatherSensingRain": 1, "weatherSensingWind": False}}}
    coord = make_coordinator(hass, data)
    assert RainSensingSwitch(coord, "SN").is_on is True
    assert WindSensingSwitch(coord, "SN").is_on is False


def test_setting_switch_is_on_bool_and_missing(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"setting": {"weatherSensingRain": True}}})
    assert RainSensingSwitch(coord, "SN").is_on is True
    # No setting slot at all -> False.
    coord2 = make_coordinator(hass, {"SN": {}})
    assert RainSensingSwitch(coord2, "SN").is_on is False


def test_reminder_switch_is_on(hass) -> None:
    data = {
        "SN": {
            "reminder": {
                "drainageReminder": "true",
                "pesticideReminder": 0,
                "taskReminder": 1,
                "waterShortageReminder": True,
            }
        }
    }
    coord = make_coordinator(hass, data)
    assert DrainageReminderSwitch(coord, "SN").is_on is True
    assert PesticideReminderSwitch(coord, "SN").is_on is False
    assert TaskReminderSwitch(coord, "SN").is_on is True
    assert WaterShortageReminderSwitch(coord, "SN").is_on is True


async def test_setting_switch_writes_via_watering_setting(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"setting": {}}})
    coord.async_set_watering_setting = AsyncMock(return_value=True)
    sw = RainSensingSwitch(coord, "SN")

    await sw.async_turn_on()
    coord.async_set_watering_setting.assert_awaited_with("SN", {"weatherSensingRain": 1})
    await sw.async_turn_off()
    coord.async_set_watering_setting.assert_awaited_with("SN", {"weatherSensingRain": 0})


async def test_reminder_switch_writes_via_reminder(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"reminder": {}}})
    coord.async_set_reminder = AsyncMock(return_value=True)
    sw = DrainageReminderSwitch(coord, "SN")

    await sw.async_turn_on()
    coord.async_set_reminder.assert_awaited_with("SN", "drainageReminder", True)
    await sw.async_turn_off()
    coord.async_set_reminder.assert_awaited_with("SN", "drainageReminder", False)


async def test_setting_switch_write_failure_raises(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"setting": {}}})
    coord.async_set_watering_setting = AsyncMock(return_value=False)
    sw = RainSensingSwitch(coord, "SN")
    with pytest.raises(HomeAssistantError):
        await sw.async_turn_on()


async def test_reminder_switch_write_failure_raises(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"reminder": {}}})
    coord.async_set_reminder = AsyncMock(return_value=False)
    sw = TaskReminderSwitch(coord, "SN")
    with pytest.raises(HomeAssistantError):
        await sw.async_turn_off()


def test_switch_available_follows_coordinator(hass) -> None:
    coord = make_coordinator(hass, {"SN": {"setting": {}}})
    sw = RainSensingSwitch(coord, "SN")
    assert sw.available is True
    coord.last_update_success = False
    assert sw.available is False


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #
async def test_async_setup_entry_creates_entities(hass) -> None:
    task = _task()
    data = {"SN": {"tasks": [task], "map": {"regions": [{"id": 1, "name": "Lawn"}]}}}
    coord = make_coordinator(hass, data)
    coord.update_interval = None  # avoid scheduling a real refresh timer
    coord.api._devices = {"SN": {"sn": "SN", "name": "Dev"}}

    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = {"coordinator": coord}

    added: list = []

    def _add(entities, update_before_add=False):
        added.extend(entities)

    await async_setup_entry(hass, coord.entry, _add)

    # 6 device-level switches + 1 schedule switch.
    assert len(added) == 7
    assert sum(isinstance(e, ScheduleSwitch) for e in added) == 1

    # A subsequent coordinator update with a NEW task adds only that task's switch.
    added.clear()
    data["SN"]["tasks"].append(_task(id=20))
    coord.async_update_listeners()
    assert len(added) == 1
    assert isinstance(added[0], ScheduleSwitch)


async def test_async_setup_entry_skips_devices_without_sn(hass) -> None:
    coord = make_coordinator(hass, {})
    coord.update_interval = None  # avoid scheduling a real refresh timer
    # One device has no sn, and a task with no id / bad id is skipped.
    coord.api._devices = {
        "x": {"name": "no-sn"},
        "SN": {"sn": "SN"},
    }
    coord.data = {"SN": {"tasks": [{"name": "no-id"}, {"id": "bad"}]}}
    coord._data = coord.data
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = {"coordinator": coord}

    added: list = []
    await async_setup_entry(hass, coord.entry, lambda e, **k: added.extend(e))

    # Only the SN device yields the 6 device-level switches; no schedule switches.
    assert sum(isinstance(e, ScheduleSwitch) for e in added) == 0
    assert len(added) == 6
