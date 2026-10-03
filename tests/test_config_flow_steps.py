"""Every step and menu option of the config flows named in strings.json has its method."""

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
