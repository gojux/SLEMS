"""Tests for the schemas of the config flow."""

from unittest.mock import AsyncMock, patch

from custom_components.slems import config_flow
from custom_components.slems.const import CONF_GRID_POWER_ENTITY, CONF_GRID_POWER_INVERTED


async def test_system_schema_builds_for_a_new_setup() -> None:
    with patch.object(
        config_flow, "async_forecast_provider_entries", AsyncMock(return_value=[])
    ):
        schema = await config_flow._async_system_schema(None, {})
    keys = {str(key): key for key in schema.schema}
    assert CONF_GRID_POWER_ENTITY in keys
    assert keys[CONF_GRID_POWER_INVERTED].default() is False


def test_entity_battery_data_keeps_only_the_chosen_control() -> None:
    data = config_flow._entity_battery_data(
        {
            "battery_control": "setpoint",
            "setpoint_entity": "number.setpoint",
            "charge_entity": "number.charge",
            "mode_entity": "select.mode",
            "mode_auto": "Auto",
            "remote_entity": "switch.remote",
            "remote_on": "Enable",
            "power_script": "script.power",
            "release_state": "auto",
            "device": "abc",
        }
    )
    assert data == {
        "battery_control": "setpoint",
        "setpoint_entity": "number.setpoint",
        "remote_entity": "switch.remote",
        "release_state": "auto",
    }


def test_read_only_entity_battery_drops_control_settings() -> None:
    data = config_flow._entity_battery_data(
        {"battery_control": "none", "release_state": "auto", "keepalive_s": 60}
    )
    assert data == {"battery_control": "none"}


def test_cleared_optional_field_is_removed() -> None:
    data = config_flow._merge(
        {"power_entity": "sensor.old", "soc_entity": "sensor.soc"},
        {"soc_entity": "sensor.soc"},
        ("soc_entity", "power_entity"),
    )
    assert data == {"soc_entity": "sensor.soc"}
