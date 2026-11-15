"""Requests to a unit of Home Assistant's Modbus backend (``async_get_unit``).

The backend neither repeats a request nor reconnects after a timeout: a
device that went silent lets every further request on the connection wait for
the timeout, until the device closes the connection itself. ``request``
therefore handles the errors like this:

* ``ModbusExceptionError`` (the device answered with an error code): final,
  the connection stays.
* ``ModbusTimeoutError`` or ``ModbusDesyncError`` (no answer or a late one):
  the connection is dropped (``disconnect()``), so the next request opens a
  new one, and the request fails. The caller's next cycle (the next poll, the
  next write of the controller) is the retry; waiting through further
  timeouts would hold the control cycle up.
* ``ModbusConnectionError`` (the connection was lost, e.g. closed by the
  device or dropped by another integration using the same device): asked
  again on a new connection after ``RETRY_WAIT_S`` (some devices refuse a
  connection right after the last one closed), at most
  ``MAX_LOST_CONNECTIONS`` times, without dropping it again.
  ``ClientClosedError`` (the unit was released) is final.

``disconnect()`` drops the connection shared with every integration using the
device; their running requests fail with ``ModbusConnectionError`` and have
to repeat themselves. So it is only called after a request without an answer.

``require_timeout`` is stored per unit id and the largest requirement of all
unit ids of the device applies: another integration asking for the same unit
id of the same device replaces the requirement.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
import logging

from modbus_connection import (
    ClientClosedError,
    ModbusConnectionError,
    ModbusDesyncError,
    ModbusTimeoutError,
    ModbusUnit,
)

_LOGGER = logging.getLogger(__name__)

FAILED_ANSWER_ERRORS = (ModbusTimeoutError, ModbusDesyncError)
MAX_LOST_CONNECTIONS = 2
RETRY_WAIT_S = 1.0


async def drop_connection(unit: ModbusUnit) -> None:
    """Drop the (shared) connection; the next request opens a new one."""
    try:
        await unit.disconnect()
    except ModbusConnectionError as err:
        # The connection is gone either way.
        _LOGGER.debug("Dropping the Modbus connection failed: %s", err)


async def request[T](unit: ModbusUnit, call: Callable[[], Awaitable[T]], description: str) -> T:
    """Result of ``call`` (a request to ``unit``), with the error handling of the module docstring.

    Only for requests that may be sent twice: reads, and writes of absolute values.
    """
    lost = 0
    while True:
        try:
            return await call()
        except FAILED_ANSWER_ERRORS as err:
            _LOGGER.debug("%s: no answer (%s), connection dropped", description, type(err).__name__)
            await drop_connection(unit)
            raise
        except ClientClosedError:
            raise
        except ModbusConnectionError as err:
            lost += 1
            if lost > MAX_LOST_CONNECTIONS:
                _LOGGER.debug("%s: connection lost %d times, given up", description, lost)
                raise
            _LOGGER.debug(
                "%s: connection lost (%s), attempt %d of %d in %.0f s",
                description, err, lost + 1, MAX_LOST_CONNECTIONS + 1, RETRY_WAIT_S,
            )
        await asyncio.sleep(RETRY_WAIT_S)


def holding_reader(unit: ModbusUnit, name: str) -> Callable[[int, int], Awaitable[list[int]]]:
    """``read(address, count)`` of holding registers through ``request``."""

    async def read(address: int, count: int) -> list[int]:
        return await request(
            unit, partial(unit.read_holding_registers, address, count), f"{name}: read of register {address}"
        )

    return read
