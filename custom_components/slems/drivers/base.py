"""Brand-agnostic battery driver contract.

The control layer only speaks two things to a battery: "give me the current
telemetry" and "deliver this signed net power". Register addresses, force modes
or entity ids never leak out of a driver.

Sign convention used everywhere in SLEMS: battery power is positive while
charging and negative while discharging.

A battery with its own PV modules feeds the house only through its output
(and may charge from the grid through an input). Its AC power is own PV +
input − output: charging from the modules is charging, and their production
counts as PV of the installation (house = grid + PV − battery stays true
without a further term).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..const import ReleaseState


@dataclass(frozen=True)
class BatteryCapabilities:
    """Static traits of a single battery unit."""

    # Usable capacity in Wh. Used by the planner for SoC <-> energy conversion.
    capacity_wh: float
    # Inclusive power envelope in W the control layer may request.
    max_charge_power_w: int
    max_discharge_power_w: int
    # False for read-only drivers; the controller never calls apply_power then.
    controllable: bool
    # Own PV modules behind the battery's output (see the module docstring).
    own_pv: bool = False
    # False: the battery charges only from its own PV modules, never from the grid.
    ac_charge: bool = True
    # Highest output into the house (W), shared by the own PV and the
    # discharge; None: no output limit apart from the discharge power.
    max_output_w: int | None = None
    # The own PV passes through to the output when the battery is full,
    # whatever the set point (it cannot be curtailed then).
    passes_pv_through: bool = False


@dataclass
class BatteryTelemetry:
    """Latest known state of a battery. Unknown values are None."""

    soc_pct: float | None = None
    # DC side battery power, +charge / -discharge.
    power_w: float | None = None
    # AC side power at the inverter output, +charge / -discharge; None if the
    # battery does not report it.
    ac_power_w: float | None = None
    # Batteries with own PV: power of the modules (>= 0), the output into the
    # house and the input from the grid (both >= 0); ac_power_w is
    # pv_w + input_w - output_w.
    pv_w: float | None = None
    output_w: float | None = None
    input_w: float | None = None
    # Driver specific extra values (voltage, temperature, counters, ...).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def grid_side_power_w(self) -> float | None:
        """Power as seen by the house installation (AC if known)."""
        return self.ac_power_w if self.ac_power_w is not None else self.power_w

    @property
    def connection_power_w(self) -> float | None:
        """Power at the battery's connection to the house (+charge / -discharge):
        input − output of a battery with own PV, else the grid side power."""
        if self.output_w is not None:
            return (self.input_w or 0.0) - self.output_w
        return self.grid_side_power_w


class BatteryDriverError(Exception):
    """Raised when a driver cannot talk to its battery."""


class BatteryDriver(ABC):
    """Abstract hardware driver for a single physical battery."""

    @property
    @abstractmethod
    def capabilities(self) -> BatteryCapabilities:
        """Return the static capabilities of this battery."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return a human readable model name."""

    @property
    def extra_telemetry_keys(self) -> frozenset[str]:
        """Keys this driver may report in BatteryTelemetry.extra."""
        return frozenset()

    @property
    def release_state(self) -> ReleaseState:
        """State the battery is left in when SLEMS releases it."""
        return ReleaseState.AUTO

    @property
    def has_connection(self) -> bool:
        """True if the driver talks to the battery itself (the communication can be paused)."""
        return False

    @property
    def min_command_interval_s(self) -> float:
        """Shortest time between two set points (e.g. rate limited cloud APIs)."""
        return 0.0

    @property
    def keepalive_s(self) -> float | None:
        """Interval of complete rewrites of the set point; None: the controller's default."""
        return None

    @property
    def start_delay_s(self) -> float | None:
        """Typical time until the grid meter shows a command out of standby, used
        until the controller learned it; None: like any other command."""
        return None

    @property
    def start_extra_s(self) -> float:
        """Time a command out of standby takes at least longer than any other
        step (the inverter starting); the learned start time never goes below."""
        return 0.0

    @property
    def reversal_extra_s(self) -> float:
        """Time the new direction of a reversal takes at least longer than any
        other step; the learned reversal time never goes below."""
        return 0.0

    async def read_device_info(self) -> dict[str, str]:
        """Firmware versions and other static device information (may be empty)."""
        return {}

    @abstractmethod
    async def connect(self) -> None:
        """Open the link to the battery. Raise BatteryDriverError on failure."""

    @abstractmethod
    async def close(self) -> None:
        """Close the link. Safe to call when already closed."""

    @abstractmethod
    async def read_telemetry(self, *, complete: bool = False) -> BatteryTelemetry:
        """Read and return the current telemetry.

        A driver may read slowly changing values less often and report their
        last value meanwhile; ``complete`` reads them now (e.g. for a self-test).
        Raise BatteryDriverError if the battery could not be reached at all.
        """

    async def apply_power(self, net_power_w: int, *, refresh: bool = False) -> bool:
        """Command a signed net power (+charge / -discharge, 0 = hold).

        The value is clamped to the capability envelope by the driver. Drivers
        may skip writing values that did not change; ``refresh`` forces a
        complete write (keep-alive). Returns True if the command was accepted.
        """
        raise NotImplementedError(f"{self.model_name} is read-only")

    def set_own_pv_state(self, *, at_top: bool, curtail_w: float) -> None:
        """Batteries with own PV, before ``apply_power``: whether the battery may
        take less than commanded (full, at the top of the charge or limited by
        temperature), so PV curtailed by a lower output has to be found again,
        and the PV to curtail on purpose (feed-in cap)."""

    def own_pv_available_w(self, pv_w: float) -> float:
        """PV the own modules could give with ``pv_w`` read: more while the
        driver curtails them on purpose (the PV read then follows the output)."""
        return pv_w

    def own_pv_diagnostics(self) -> dict | None:
        """State of a battery with own PV for the diagnostics (None without)."""
        return None

    def needs_write(self, net_power_w: int) -> bool:
        """True if the set point for ``net_power_w`` differs from the one
        written although the command did not change (e.g. an output that
        follows the own PV)."""
        return False

    async def release_control(self) -> None:
        """Hand control back to the battery's internal logic.

        Called when SLEMS stops controlling the battery (mode change, unload).
        Read-only drivers have nothing to release. Raises
        ``BatteryDriverError`` if the battery could not be released for sure;
        the coordinator then tries again.
        """
