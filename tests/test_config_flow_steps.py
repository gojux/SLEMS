"""Config flows: every step and menu option named in strings.json has its method, and the
forms build without errors."""

import json
from pathlib import Path

from custom_components.slems import config_flow

STRINGS = json.loads((Path(config_flow.__file__).parent / "strings.json").read_text())
FLOWS = {"tariff": config_flow.TariffSubentryFlow}


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


def test_tariff_prompt_link_in_the_language_of_home_assistant() -> None:
    from custom_components.slems.config_flow import tariff_prompt_url

    assert tariff_prompt_url("de").endswith("/docs/tariff-prompt.de.md")
    assert tariff_prompt_url("en").endswith("/docs/tariff-prompt.md")
    assert tariff_prompt_url(None).endswith("/docs/tariff-prompt.md")
    for name in ("strings.json", "translations/de.json"):
        texts = json.loads((Path(config_flow.__file__).parent / name).read_text())
        assert "{prompt_url}" in texts["config_subentries"]["tariff"]["step"]["template_energy"]["description"]
