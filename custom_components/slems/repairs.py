"""Dialogs of the repair issues that ask something: the phase of a battery or consumer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import DOMAIN
from .phases import Phase, label


class PhaseFlow(RepairsFlow):
    """Which phase a battery or consumer is connected to: the measured one is preselected."""

    def __init__(self, subentry_id: str) -> None:
        self._subentry_id = subentry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entries = self.hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return self.async_abort(reason="not_loaded")
        coordinator = entries[0].runtime_data
        learner = coordinator.phase_learner(self._subentry_id)
        if user_input is not None:
            coordinator.choose_phase(self._subentry_id, Phase(user_input["phase"]))
            return self.async_create_entry(data={})
        measured = learner.changed_to or learner.confirm or learner.phase
        if measured is None:
            # Answered meanwhile (e.g. learned anew).
            return self.async_abort(reason="not_needed")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("phase", default=measured.value): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=[p.value for p in Phase], translation_key="phase")
                    )
                }
            ),
            description_placeholders={
                "name": coordinator.phase_devices.get(self._subentry_id, "–"),
                "phase": label(learner.phase) if learner.phase else "–",
                "measured": label(measured),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """The dialog of a SLEMS repair issue."""
    return PhaseFlow(str((data or {})["subentry_id"]))
