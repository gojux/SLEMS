"""Diagnostics download: addresses in the configuration are redacted."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.slems import diagnostics


async def test_the_addresses_of_the_configuration_are_redacted() -> None:
    entry = SimpleNamespace(
        data={"grid_modbus_host": "192.168.1.30", "grid_modbus_port": 502},
        options={"grid_modbus_host": "192.168.1.31"},
        subentries={
            "b1": SimpleNamespace(subentry_type="battery", title="Venus", data={"host": "192.168.1.20", "port": 502}),
        },
        runtime_data=SimpleNamespace(batteries=[]),
    )
    with patch.object(diagnostics, "_system", return_value={}), patch.object(diagnostics, "_phases", return_value=None):
        result = await diagnostics.async_get_config_entry_diagnostics(None, entry)
    assert result["entry"]["data"] == {"grid_modbus_host": "**REDACTED**", "grid_modbus_port": 502}
    assert result["entry"]["options"] == {"grid_modbus_host": "**REDACTED**"}
    assert result["entry"]["subentries"][0]["data"] == {"host": "**REDACTED**", "port": 502}
