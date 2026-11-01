"""Tests for the schemas of the config flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.slems import config_flow
from custom_components.slems.const import (
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_INVERTED,
    CONF_HOLIDAY_REGION,
)


def _hass(country: str | None = None, **extra) -> SimpleNamespace:
    async def executor(func, *args):
        return func(*args)

    return SimpleNamespace(config=SimpleNamespace(country=country), async_add_executor_job=executor, **extra)


async def test_system_schema_builds_for_a_new_setup() -> None:
    with patch.object(
        config_flow, "async_forecast_provider_entries", AsyncMock(return_value=[])
    ):
        schema = await config_flow._async_system_schema(_hass(), {})
    keys = {str(key): key for key in schema.schema}
    assert CONF_GRID_POWER_ENTITY in keys
    assert keys[CONF_GRID_POWER_INVERTED].default() is False
    # No country in Home Assistant: no regions to choose from.
    assert CONF_HOLIDAY_REGION not in keys


async def test_holiday_region_offers_the_regions_of_the_country() -> None:
    with patch.object(config_flow, "async_forecast_provider_entries", AsyncMock(return_value=[])):
        schema = await config_flow._async_system_schema(_hass("AT"), {CONF_HOLIDAY_REGION: "8"})
        keys = {str(key): key for key in schema.schema}
        assert keys[CONF_HOLIDAY_REGION].default() == "8"
        options = schema.schema[keys[CONF_HOLIDAY_REGION]].config["options"]
        assert options[0]["value"] == "national"
        assert {"value": "8", "label": "Vorarlberg"} in options
        # A region of another country (Home Assistant moved): the national holidays.
        schema = await config_flow._async_system_schema(_hass("AT"), {CONF_HOLIDAY_REGION: "BY"})
        keys = {str(key): key for key in schema.schema}
        assert keys[CONF_HOLIDAY_REGION].default() == "national"


async def test_holiday_region_suggested_once_when_not_stored() -> None:
    flow = config_flow.SlemsOptionsFlow.__new__(config_flow.SlemsOptionsFlow)
    flow.hass = _hass("AT", states=SimpleNamespace(get=lambda entity_id: None))
    flow._entry = lambda: None
    flow._suggested = {}
    flow._devices_changed = False
    suggest = AsyncMock(return_value="8")
    with (
        patch.object(config_flow, "async_forecast_provider_entries", AsyncMock(return_value=[])),
        patch.object(config_flow, "async_suggest_region", suggest),
    ):
        flow._current = {}
        result = await flow.async_step_system()
        keys = {str(key): key for key in result["data_schema"].schema}
        assert keys[CONF_HOLIDAY_REGION].default() == "8"
        await flow.async_step_system()
        assert suggest.await_count == 1
        # A stored choice is kept, without a lookup.
        flow._region_lookup = None
        flow._current = {CONF_HOLIDAY_REGION: "national"}
        result = await flow.async_step_system()
        keys = {str(key): key for key in result["data_schema"].schema}
        assert keys[CONF_HOLIDAY_REGION].default() == "national"
        assert suggest.await_count == 1


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


def test_entity_battery_with_own_pv_keeps_its_entities() -> None:
    own_pv = {
        "own_pv_entity": "sensor.pv",
        "output_entity": "sensor.output",
        "max_output_w": 800.0,
    }
    data = config_flow._entity_battery_data(
        {
            "battery_control": "output",
            "own_pv": True,
            **own_pv,
            "output_setpoint_entity": "number.output",
            "setpoint_entity": "number.setpoint",
            "remove_from_pv": True,
        }
    )
    assert data == {
        "battery_control": "output",
        "own_pv": True,
        **own_pv,
        "max_output_w": 800,
        "output_setpoint_entity": "number.output",
    }
    # Switched off again: its entities go.
    data = config_flow._entity_battery_data({"battery_control": "none", "own_pv": False, **own_pv})
    assert data == {"battery_control": "none"}


def test_own_pv_entered_as_pv_system_is_found_and_removed() -> None:
    from types import SimpleNamespace

    flow = config_flow.BatterySubentryFlow()
    updates = []
    entry = SimpleNamespace(data={"pv_power_entity": ["sensor.roof", "sensor.output"]}, options={})
    flow._get_entry = lambda: entry
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=lambda e, **kw: updates.append(kw))
    )
    flow._data = {"own_pv_entity": "sensor.pv", "output_entity": "sensor.output"}
    assert flow._counted_as_pv() == ["sensor.output"]
    flow._remove_from_pv = flow._counted_as_pv()
    flow._remove_pv_systems()
    assert updates == [{"data": {"pv_power_entity": ["sensor.roof"]}}]


def test_output_control_keeps_its_input_and_mode() -> None:
    data = config_flow._entity_battery_data(
        {
            "battery_control": "output",
            "own_pv": True,
            "own_pv_entity": "sensor.pv",
            "output_entity": "sensor.output",
            "input_entity": "sensor.input",
            "max_output_w": 800.0,
            "passes_pv_through": True,
            "own_pv_forecast_entry": "forecast",
            "output_setpoint_entity": "number.output",
            "input_setpoint_entity": "number.input",
            "mode_entity": "select.ac_mode",
            "mode_charge": "Input",
            "charge_entity": "number.charge",
        }
    )
    assert "charge_entity" not in data
    assert data["mode_entity"] == "select.ac_mode" and data["mode_charge"] == "Input"
    assert data["input_setpoint_entity"] == "number.input" and data["own_pv_forecast_entry"] == "forecast"
    # Without own PV the input and the forecast go too.
    data = config_flow._entity_battery_data({"battery_control": "none", "input_entity": "sensor.input", "own_pv_forecast_entry": "f"})
    assert data == {"battery_control": "none"}


async def test_own_pv_of_a_battery_cannot_be_entered_as_pv_system() -> None:
    battery = SimpleNamespace(
        title="Balcony", data={"own_pv": True, "own_pv_entity": "sensor.modules", "output_entity": "sensor.output"}
    )
    entry = SimpleNamespace(subentries={"b": battery, "c": SimpleNamespace(title="Car", data={})})
    assert config_flow._own_pv_owners(entry) == {"sensor.modules": "Balcony", "sensor.output": "Balcony"}
    assert config_flow._own_pv_owners(None) == {}

    flow = config_flow.SlemsOptionsFlow.__new__(config_flow.SlemsOptionsFlow)
    flow.hass = _hass(states=SimpleNamespace(get=lambda entity_id: None))
    flow._entry = lambda: entry
    flow._current = {}
    done = AsyncMock(return_value="done")
    flow._async_system_done = done
    with patch.object(config_flow, "async_forecast_provider_entries", AsyncMock(return_value=[])):
        result = await flow.async_step_system({"grid_power_entity": "sensor.grid", "pv_power_entity": ["sensor.roof", "sensor.output"]})
        assert result["errors"] == {"pv_power_entity": "pv_is_own_pv"}
        assert result["description_placeholders"]["own_pv"] == "sensor.output (Balcony)"
        done.assert_not_called()
        assert await flow.async_step_system({"grid_power_entity": "sensor.grid", "pv_power_entity": ["sensor.roof"]}) == "done"
