"""Config flow: name and peak power of a simulated PV system."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from . import DOMAIN


class SimSolarForecastFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input["name"])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input["name"], data={"peak_w": user_input["peak_w"]})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("name"): str, vol.Required("peak_w", default=1000): vol.Coerce(float)}
            ),
        )
