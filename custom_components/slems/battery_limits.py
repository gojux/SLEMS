"""Operating limits of one battery: SoC window, power limits and temperature.

* SoC window: no discharge at or below the minimum SoC and no charge at or
  above the maximum SoC (a maximum of 100 % leaves the end of charge to the
  BMS). After reaching a limit the battery is released again only
  ``SOC_REENTRY_MARGIN_PCT`` away from it: the resting SoC rebounds after a
  load, which would otherwise switch the battery on and off in quick
  succession (as in Omnibattery).
* Power limits: user limits of the AC charge and discharge power, e.g. 800 W
  for a plug-in system; never above the capability of the battery.
* Temperature (optional; the high limit after Omnibattery's temperature charge
  limit): above the high limit the charge power ramps linearly down to the
  floor at high limit + band. Below the low limit no charging; above it the
  power ramps back to full within ``LOW_TEMPERATURE_BAND_C``. The Venus
  reports its internal temperature, not the cell temperature; the BMS keeps
  its own protection.
"""

from __future__ import annotations

from dataclasses import dataclass

SOC_REENTRY_MARGIN_PCT = 2.0
LOW_TEMPERATURE_BAND_C = 5.0


@dataclass
class BatteryLimitSettings:
    """User settings of one battery (restored after a restart)."""

    min_soc_pct: float = 12.0
    max_soc_pct: float = 100.0
    # None: the maximum power of the battery.
    charge_limit_w: float | None = None
    discharge_limit_w: float | None = None


@dataclass(frozen=True)
class TemperatureLimit:
    enabled: bool = False
    high_c: float = 40.0
    band_c: float = 10.0
    floor_pct: float = 40.0
    low_c: float = 0.0


def temperature_factor(temperature_c: float | None, limit: TemperatureLimit) -> float:
    """Share (0..1) of the charge power allowed at ``temperature_c``.

    Without a temperature or when disabled: 1 (a sensor gap never throttles).
    """
    if not limit.enabled or temperature_c is None:
        return 1.0
    if temperature_c <= limit.low_c:
        return 0.0
    if temperature_c < limit.low_c + LOW_TEMPERATURE_BAND_C:
        return (temperature_c - limit.low_c) / LOW_TEMPERATURE_BAND_C
    if temperature_c <= limit.high_c:
        return 1.0
    floor = limit.floor_pct / 100
    if limit.band_c <= 0 or temperature_c >= limit.high_c + limit.band_c:
        return floor
    return 1.0 - (temperature_c - limit.high_c) / limit.band_c * (1.0 - floor)


class SocWindow:
    """Charge/discharge blocked by the SoC window, with re-entry margin."""

    def __init__(self) -> None:
        self.charge_blocked = False
        self.discharge_blocked = False

    def update(self, soc_pct: float | None, settings: BatteryLimitSettings) -> None:
        if soc_pct is None:
            return
        minimum, maximum = settings.min_soc_pct, settings.max_soc_pct
        if minimum > 0 and soc_pct <= minimum:
            self.discharge_blocked = True
        elif minimum <= 0 or soc_pct >= minimum + SOC_REENTRY_MARGIN_PCT:
            self.discharge_blocked = False
        if maximum < 100 and soc_pct >= maximum:
            self.charge_blocked = True
        elif maximum >= 100 or soc_pct <= maximum - SOC_REENTRY_MARGIN_PCT:
            self.charge_blocked = False


@dataclass(frozen=True)
class PowerLimits:
    """Allowed AC power of one battery right now (W, both ≥ 0)."""

    charge_w: float
    discharge_w: float
    # Why charging/discharging is limited: "soc", "power", "temperature".
    charge_reason: str | None = None
    discharge_reason: str | None = None


def power_limits(
    max_charge_w: float,
    max_discharge_w: float,
    settings: BatteryLimitSettings,
    window: SocWindow,
    temperature_c: float | None,
    temperature: TemperatureLimit,
    *,
    use_soc_window: bool = True,
    full_charge: bool = False,
) -> PowerLimits:
    """Combine capability, user power limits, SoC window and temperature.

    ``full_charge``: the battery is due for its regular full charge and may
    charge above its maximum SoC.
    """
    charge, charge_reason = max_charge_w, None
    if settings.charge_limit_w is not None and settings.charge_limit_w < charge:
        charge, charge_reason = settings.charge_limit_w, "power"
    factor = temperature_factor(temperature_c, temperature)
    if factor < 1.0:
        charge, charge_reason = charge * factor, "temperature"
    if use_soc_window and window.charge_blocked and not full_charge:
        charge, charge_reason = 0.0, "soc"

    discharge, discharge_reason = max_discharge_w, None
    if settings.discharge_limit_w is not None and settings.discharge_limit_w < discharge:
        discharge, discharge_reason = settings.discharge_limit_w, "power"
    if use_soc_window and window.discharge_blocked:
        discharge, discharge_reason = 0.0, "soc"
    return PowerLimits(max(0.0, charge), max(0.0, discharge), charge_reason, discharge_reason)
