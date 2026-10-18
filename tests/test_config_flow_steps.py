"""Config flows: every step and menu option named in strings.json has its method, and the
forms build without errors."""

import json
from pathlib import Path

from custom_components.slems import config_flow

STRINGS = json.loads((Path(config_flow.__file__).parent / "strings.json").read_text())
FLOWS = {
    "tariff": config_flow.TariffSubentryFlow,
    "consumer": config_flow.ConsumerSubentryFlow,
    "battery": config_flow.BatterySubentryFlow,
}


def test_steps_and_menu_options_have_methods() -> None:
    for name, flow in FLOWS.items():
        steps = STRINGS["config_subentries"][name]["step"]
        for step_id, step in steps.items():
            assert hasattr(flow, f"async_step_{step_id}"), f"{name}: step {step_id}"
            for option in step.get("menu_options", {}):
                assert hasattr(flow, f"async_step_{option}"), f"{name}: menu option {option} of {step_id}"


def test_battery_limits_show_the_currency_of_home_assistant() -> None:
    from types import SimpleNamespace

    from custom_components.slems.config_flow import BatterySubentryFlow
    from custom_components.slems.const import CONF_PURCHASE_PRICE_EUR, EfficiencyMode

    flow = BatterySubentryFlow.__new__(BatterySubentryFlow)
    flow.hass = SimpleNamespace(config=SimpleNamespace(currency="CHF"))
    schema = flow._limits_schema({}, 2500, list(EfficiencyMode))
    price = next(value for key, value in schema.items() if str(key) == CONF_PURCHASE_PRICE_EUR)
    assert price.config["unit_of_measurement"] == "CHF"


def test_no_static_method_uses_self() -> None:
    import ast
    from pathlib import Path

    import custom_components.slems as package

    for path in Path(package.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                isinstance(d, ast.Name) and d.id in ("staticmethod", "classmethod") for d in node.decorator_list
            ):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                assert "self" not in names, f"{path.name}: {node.name}"


def test_reconfigured_battery_with_the_same_address_is_not_probed() -> None:
    from types import SimpleNamespace

    from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER

    from custom_components.slems.config_flow import BatterySubentryFlow

    flow = BatterySubentryFlow.__new__(BatterySubentryFlow)
    data = {"host": "192.0.2.10", "port": 502, "unit_id": 1}
    flow._get_reconfigure_subentry = lambda: SimpleNamespace(data=data)
    flow.context = {"source": SOURCE_RECONFIGURE}
    # The running battery holds its only Modbus connection.
    assert flow._same_connection(dict(data))
    assert not flow._same_connection({**data, "host": "192.0.2.11"})
    flow.context = {"source": SOURCE_USER}
    assert not flow._same_connection(dict(data))


def test_item_form_shows_the_symbols_of_the_currency() -> None:
    from custom_components.slems.config_flow import _price_symbols

    assert _price_symbols("EUR") == ("€", "ct")
    assert _price_symbols("CHF") == ("CHF", "Rp.")
    assert _price_symbols("SEK") == ("SEK", "1/100 SEK")


def test_tariff_prompt_link_in_the_language_of_home_assistant() -> None:
    from custom_components.slems.config_flow import tariff_prompt_url

    assert tariff_prompt_url("de").endswith("/docs/tariff-prompt.de.md")
    assert tariff_prompt_url("en").endswith("/docs/tariff-prompt.md")
    assert tariff_prompt_url(None).endswith("/docs/tariff-prompt.md")
    for name in ("strings.json", "translations/de.json"):
        texts = json.loads((Path(config_flow.__file__).parent / name).read_text())
        assert "{prompt_url}" in texts["config_subentries"]["tariff"]["step"]["template_energy"]["description"]


def test_select_options_named_as_their_integration_shows_them() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    entry = SimpleNamespace(platform="evcc_intg", domain="select", translation_key="mode")
    registry = SimpleNamespace(async_get=lambda _entity_id: entry)
    strings = {"component.evcc_intg.entity.select.mode.state.now": "Schnell"}

    async def translations(*_args):
        return strings

    hass = SimpleNamespace(config=SimpleNamespace(language="de"))
    with (
        patch.object(config_flow.er, "async_get", lambda _hass: registry),
        patch.object(config_flow, "async_get_translations", translations),
    ):
        options = asyncio.run(config_flow._option_labels(hass, "select.evcc_garage_mode", ["off", "now"]))
        assert [(o["value"], o["label"]) for o in options] == [("off", "off"), ("now", "Schnell")]
        # Without a registry entry (e.g. an input_select from YAML): the options themselves.
        registry.async_get = lambda _entity_id: None
        options = asyncio.run(config_flow._option_labels(hass, "input_select.mode", ["a"]))
        assert [(o["value"], o["label"]) for o in options] == [("a", "a")]



def test_hot_water_states_preselected_by_name() -> None:
    from custom_components.slems.config_flow import hot_water_guess

    def option(value: str, label: str | None = None) -> dict:
        return {"value": value, "label": label or value}

    hoval = [option(v) for v in ("switched_off", "heating_mode", "hot_water", "defrost", "frost_protection")]
    assert hot_water_guess(hoval) == ["hot_water"]
    # By the label its integration shows, or other names for it.
    assert hot_water_guess([option("3", "Warmwasser"), option("1", "Heizen")]) == ["3"]
    assert hot_water_guess([option("DHW"), option("Heating")]) == ["DHW"]
    assert hot_water_guess([option("heating"), option("cooling")]) == []


def test_heat_pump_release_asks_for_its_hot_water_states() -> None:
    import asyncio

    from custom_components.slems.config_flow import ConsumerSubentryFlow
    from custom_components.slems.const import (
        CONF_CONSUMER_TYPE,
        CONF_CONTROL_MODE,
        CONF_MODE_ENTITY,
        CONF_NOMINAL_POWER_W,
        CONF_PRIORITY,
        ControlMode,
    )

    flow = ConsumerSubentryFlow()
    flow._existing = lambda: {}
    flow._data = {CONF_CONSUMER_TYPE: "heat_pump", CONF_CONTROL_MODE: ControlMode.RELEASE.value}

    async def states():
        return "hot_water_states"

    flow.async_step_hot_water_states = states
    result = asyncio.run(
        flow.async_step_control({CONF_MODE_ENTITY: "sensor.hp_status", CONF_NOMINAL_POWER_W: 2500, CONF_PRIORITY: 1})
    )
    assert result == "hot_water_states"


def _control_form(data: dict, infos: list, existing: dict | None = None, device_changed: bool = False) -> dict:
    import asyncio

    from custom_components.slems.config_flow import ConsumerSubentryFlow

    flow = ConsumerSubentryFlow()
    flow._existing = lambda: dict(existing or {})
    flow._data = data
    flow._infos = infos
    flow._device_changed = device_changed
    flow.handler = ("entry", "consumer")
    result = asyncio.run(flow.async_step_control())
    return {str(key): key.default() if callable(key.default) else key.description for key in result["data_schema"].schema}


def test_heat_pump_without_hot_water_has_no_operating_mode() -> None:
    from custom_components.slems.const import CONF_CONSUMER_TYPE, CONF_CONTROL_MODE, CONF_HOT_WATER, CONF_MODE_ENTITY

    data = {CONF_CONSUMER_TYPE: "heat_pump", CONF_CONTROL_MODE: "release"}
    assert CONF_MODE_ENTITY not in _control_form({**data, CONF_HOT_WATER: False}, [])
    assert CONF_MODE_ENTITY in _control_form({**data, CONF_HOT_WATER: True}, [])


def test_control_step_shows_the_suggestions_of_the_device() -> None:
    from test_entity_match import ELWA

    from custom_components.slems.const import (
        CONF_BLOCK_ENTITY,
        CONF_CONSUMER_TYPE,
        CONF_CONTROL_ENTITY,
        CONF_CONTROL_MODE,
        CONF_MAX_POWER_W,
        CONF_TEMPERATURE_ENTITY,
    )

    data = {CONF_CONSUMER_TYPE: "heating_rod", CONF_CONTROL_MODE: "power"}
    form = _control_form(data, ELWA)
    assert form[CONF_CONTROL_ENTITY] == "number.elwa_2_power"
    assert form[CONF_MAX_POWER_W] == 3500
    assert form[CONF_TEMPERATURE_ENTITY] == {"suggested_value": "sensor.elwa_2_temperature"}
    assert form[CONF_BLOCK_ENTITY] == {"suggested_value": "water_heater.elwa_2"}
    # Reconfigured with the same device: what was stored stays; another device: its suggestions.
    existing = {CONF_CONTROL_MODE: "power", CONF_CONTROL_ENTITY: "number.other", CONF_MAX_POWER_W: 2000}
    assert _control_form(data, ELWA, existing)[CONF_CONTROL_ENTITY] == "number.other"
    assert _control_form(data, ELWA, existing, device_changed=True)[CONF_CONTROL_ENTITY] == "number.elwa_2_power"


def test_devices_listed_by_fit_with_their_power() -> None:
    from test_entity_match import ELWA, HOVAL, SHELLY_PLUG, SMART_METER

    from custom_components.slems.config_flow import _device_select, _DeviceCandidate
    from custom_components.slems.const import ConsumerType
    from custom_components.slems.entity_match import consumer_device_fit

    candidates = {
        "plug": _DeviceCandidate("Dryer", "shelly", SHELLY_PLUG),
        "meter": _DeviceCandidate("Meter", "shelly", SMART_METER),
        "rod": _DeviceCandidate("ELWA 2", "mypv_elwa2_modbus", ELWA),
        "hp": _DeviceCandidate("Belaria", "hoval_unofficial", HOVAL),
    }
    select = _device_select(candidates, lambda infos: consumer_device_fit(infos, ConsumerType.HEATING_ROD), None)
    options = [(o["value"], o["label"]) for o in select.config["options"]]
    assert options == [("rod", "ELWA 2 (mypv_elwa2_modbus) – 1800 W"), ("plug", "Dryer (shelly) – 0.0 W")]
    # The stored device is always offered.
    select = _device_select(candidates, lambda infos: consumer_device_fit(infos, ConsumerType.HEATING_ROD), "meter")
    assert "meter" in [o["value"] for o in select.config["options"]]
    assert _device_select(candidates, lambda _infos: 0, None) is None


def test_battery_takes_the_energy_dashboards_sensors() -> None:
    from unittest.mock import patch

    from custom_components.slems import config_flow
    from custom_components.slems.const import (
        CONF_CHARGED_ENERGY_ENTITY,
        CONF_DISCHARGED_ENERGY_ENTITY,
        CONF_POWER_ENTITY,
        CONF_POWER_INVERTED,
        CONF_SOC_ENTITY,
    )
    from custom_components.slems.energy_dashboard import DashboardBattery, EnergyDashboard

    dashboard = EnergyDashboard(
        batteries=(
            DashboardBattery(soc="sensor.a_soc", power="sensor.a_power", charged="sensor.a_in", discharged="sensor.a_out"),
            DashboardBattery(soc="sensor.b_soc", power="sensor.b_power", power_discharge_positive=False),
        )
    )
    devices = {"sensor.a_soc": "inverter", "sensor.a_power": "inverter", "sensor.b_soc": "b", "sensor.b_power": "b"}
    with patch.object(config_flow, "_device_of", lambda _hass, entity_id: devices.get(entity_id)):
        take = config_flow._battery_from_dashboard
        assert take(None, dashboard, "inverter", {}, set()) == {
            CONF_SOC_ENTITY: "sensor.a_soc",
            CONF_CHARGED_ENERGY_ENTITY: "sensor.a_in",
            CONF_DISCHARGED_ENERGY_ENTITY: "sensor.a_out",
            CONF_POWER_ENTITY: "sensor.a_power",
            CONF_POWER_INVERTED: True,
        }
        # The device suggests another power sensor (e.g. the AC side): it stays, without the dashboard's sign.
        assert CONF_POWER_ENTITY not in take(None, dashboard, "inverter", {CONF_POWER_ENTITY: "sensor.ac"}, set())
        # Without a device: the first battery SLEMS does not use yet.
        assert take(None, dashboard, None, {}, {"sensor.a_soc"}) == {
            CONF_SOC_ENTITY: "sensor.b_soc",
            CONF_POWER_ENTITY: "sensor.b_power",
            CONF_POWER_INVERTED: False,
        }
        assert take(None, dashboard, "other", {}, set()) == {}
