"""Brand-agnostic battery driver contract.

The control layer only speaks two things to a battery: "give me the current
telemetry" and "deliver this signed net power". Register addresses, force modes
or entity ids never leak out of a driver.

Sign convention used everywhere in SLEMS: battery power is positive while
charging and negative while discharging.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class BatteryTelemetry:
    """Latest known state of a battery. Unknown values are None."""

    soc_pct: float | None = None
    # DC side battery power, +charge / -discharge.
    power_w: float | None = None
    # AC side power at the inverter output, +charge / -discharge; None if the
    # battery does not report it.
    ac_power_w: float | None = None
    # Driver specific extra values (voltage, temperature, counters, ...).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def grid_side_power_w(self) -> float | None:
        """Power as seen by the house installation (AC if known)."""
        return self.ac_power_w if self.ac_power_w is not None else self.power_w


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
    def has_connection(self) -> bool:
        """True if the driver talks to the battery itself (the communication can be paused)."""
        return False

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
    async def read_telemetry(self) -> BatteryTelemetry:
        """Read and return the current telemetry.

        Raise BatteryDriverError if the battery could not be reached at all.
        """

    async def apply_power(self, net_power_w: int, *, refresh: bool = False) -> bool:
        """Command a signed net power (+charge / -discharge, 0 = hold).

        The value is clamped to the capability envelope by the driver. Drivers
        may skip writing values that did not change; ``refresh`` forces a
        complete write (keep-alive). Returns True if the command was accepted.
        """
        raise NotImplementedError(f"{self.model_name} is read-only")

    async def release_control(self) -> None:
        """Hand control back to the battery's internal logic.

        Called when SLEMS stops controlling the battery (mode change, unload).
        Read-only drivers have nothing to release.
        """
