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
