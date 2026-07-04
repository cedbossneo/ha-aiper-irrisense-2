"""Tests for the Aiper Irrisense 2 config and options flows.

Every path that would hit the Aiper cloud goes through ``IrrisenseApi`` inside
``config_flow`` (login / get_devices / disconnect on the executor). We patch the
class at the ``config_flow`` module boundary so no network happens and the flow
logic is exercised deterministically.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aiper_irrisense.const import (
    CONF_ENABLE_MQTT,
    CONF_HISTORY_REFRESH_HOURS,
    CONF_MAP_REFRESH_HOURS,
    CONF_MQTT_DEBUG,
    CONF_POLL_INTERVAL,
    CONF_REGION,
    CONF_REMINDER_REFRESH_HOURS,
    DOMAIN,
)

CONFIG_FLOW = "custom_components.aiper_irrisense.config_flow"

USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "hunter2",
    CONF_REGION: "eu",
}


def _mock_api(*, login=True, devices=None):
    """Build a patch context for IrrisenseApi with the given behaviour."""
    patcher = patch(f"{CONFIG_FLOW}.IrrisenseApi")
    mock_cls = patcher.start()
    inst = mock_cls.return_value
    inst.login.return_value = login
    inst.get_devices.return_value = devices if devices is not None else []
    inst.disconnect.return_value = None
    return patcher, mock_cls, inst


# --------------------------------------------------------------------------- #
# async_step_user
# --------------------------------------------------------------------------- #


async def test_user_step_shows_form(hass) -> None:
    """Initial user step (no input) renders the credentials form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_user_step_happy_path(hass) -> None:
    """Valid creds + at least one device → creates the config entry."""
    patcher, _cls, inst = _mock_api(
        login=True, devices=[{"sn": "WRX123", "name": "Irrisense"}]
    )
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    finally:
        patcher.stop()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Aiper Irrisense (user@example.com)"
    assert result2["data"] == USER_INPUT
    assert result2["result"].unique_id == "user@example.com"
    # login + get_devices + disconnect all ran on the executor.
    inst.login.assert_called_once()
    inst.get_devices.assert_called_once()
    inst.disconnect.assert_called_once()


async def test_user_step_invalid_auth(hass) -> None:
    """login() returning falsy → InvalidAuth → form re-shown with error."""
    patcher, _cls, inst = _mock_api(login=False)
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    finally:
        patcher.stop()

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}
    # get_devices never reached, but disconnect must still run (finally block).
    inst.get_devices.assert_not_called()
    inst.disconnect.assert_called_once()


async def test_user_step_login_raises_is_invalid_auth(hass) -> None:
    """A raising login() is mapped to InvalidAuth by validate_input."""
    patcher, _cls, inst = _mock_api()
    inst.login.side_effect = RuntimeError("cloud rejected")
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    finally:
        patcher.stop()

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}
    inst.disconnect.assert_called_once()


async def test_user_step_no_devices(hass) -> None:
    """Authenticated but no Irrisense devices → 'no_devices' error."""
    patcher, _cls, _inst = _mock_api(login=True, devices=[])
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    finally:
        patcher.stop()

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "no_devices"}


async def test_user_step_unknown_error(hass) -> None:
    """An error before validate_input's try (API construction) → 'unknown'."""
    with patch(f"{CONFIG_FLOW}.IrrisenseApi", side_effect=Exception("boom")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}


async def test_user_step_duplicate_aborts(hass) -> None:
    """Second entry with the same username is aborted as already_configured."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data=USER_INPUT,
    )
    existing.add_to_hass(hass)

    patcher, _cls, _inst = _mock_api(
        login=True, devices=[{"sn": "WRX123"}]
    )
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    finally:
        patcher.stop()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# --------------------------------------------------------------------------- #
# Reauth flow
# --------------------------------------------------------------------------- #


def _start_reauth(hass, entry):
    return hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )


async def test_reauth_success(hass) -> None:
    """Reauth with valid creds updates the entry and aborts successfully."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=USER_INPUT
    )
    entry.add_to_hass(hass)

    patcher, _cls, _inst = _mock_api(login=True, devices=[{"sn": "WRX123"}])
    # async_update_reload_and_abort schedules a reload → stub out setup so no
    # real integration setup (and no real network) runs.
    with patch(
        "custom_components.aiper_irrisense.async_setup_entry", return_value=True
    ):
        try:
            result = await _start_reauth(hass, entry)
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "reauth_confirm"

            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PASSWORD: "new-pass"}
            )
            await hass.async_block_till_done()
        finally:
            patcher.stop()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-pass"


async def test_reauth_invalid_auth(hass) -> None:
    """Reauth with bad creds re-shows the confirm form with an error."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=USER_INPUT
    )
    entry.add_to_hass(hass)

    patcher, _cls, _inst = _mock_api(login=False)
    try:
        result = await _start_reauth(hass, entry)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )
    finally:
        patcher.stop()

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "reauth_confirm"
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_reauth_unknown_error(hass) -> None:
    """A non-auth error during reauth surfaces as the 'unknown' error."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=USER_INPUT
    )
    entry.add_to_hass(hass)

    with patch(f"{CONFIG_FLOW}.IrrisenseApi", side_effect=Exception("boom")):
        result = await _start_reauth(hass, entry)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "whatever"}
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}


# --------------------------------------------------------------------------- #
# Options flow
# --------------------------------------------------------------------------- #


async def test_options_flow_shows_form(hass) -> None:
    """Options flow init renders the settings form."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves_values(hass) -> None:
    """Submitting the options form stores the chosen values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={CONF_ENABLE_MQTT: False},  # exercise the current.get() default path
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    submitted = {
        CONF_ENABLE_MQTT: True,
        CONF_MQTT_DEBUG: True,
        CONF_POLL_INTERVAL: 300,
        CONF_MAP_REFRESH_HOURS: 12,
        CONF_HISTORY_REFRESH_HOURS: 8,
        CONF_REMINDER_REFRESH_HOURS: 48,
    }
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"] == submitted
