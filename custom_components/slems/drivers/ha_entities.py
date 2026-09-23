"""Read-only battery backed by existing Home Assistant entities.

Useful while another integration (e.g. Omnibattery) still owns the Modbus
connection: SLEMS can observe the battery and run in simulation mode without
opening a second connection to a device that only has one TCP slot.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..util import state_as_float, state_as_watts
from .base import BatteryCapabilities, BatteryDriver, BatteryDriverError, BatteryTelemetry


class HomeAssistantEntityDriver(BatteryDriver):
    """Battery whose state comes from Home Assistant sensor entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        soc_entity_id: str,
        power_entity_id: str | None,
        *,
        power_inverted: bool,
        capacity_wh: float,
        max_charge_power_w: int,
        max_discharge_power_w: int,
    ) -> None:
        self._hass = hass
        self._soc_entity_id = soc_entity_id
        self._power_entity_id = power_entity_id
        self._power_inverted = power_inverted
        self._capabilities = BatteryCapabilities(
            capacity_wh=capacity_wh,
            max_charge_power_w=max_charge_power_w,
            max_discharge_power_w=max_discharge_power_w,
            controllable=False,
        )

    @property
    def capabilities(self) -> BatteryCapabilities:
        return self._capabilities

    @property
    def model_name(self) -> str:
        return "Home Assistant entities (read-only)"

    async def connect(self) -> None:
        """Nothing to connect."""

    async def close(self) -> None:
        """Nothing to close."""

    async def read_telemetry(self) -> BatteryTelemetry:
        soc = state_as_float(self._hass.states.get(self._soc_entity_id))
        if soc is None:
            raise BatteryDriverError(f"{self._soc_entity_id} is unavailable")
        power = None
        if self._power_entity_id:
            power = state_as_watts(self._hass.states.get(self._power_entity_id))
            if power is not None and self._power_inverted:
                power = -power
        return BatteryTelemetry(soc_pct=soc, power_w=power)
