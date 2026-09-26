"""Finding Marstek Venus batteries in the local network.

Modbus TCP has no discovery protocol. The search opens TCP port 502 on every
address of the networks Home Assistant uses (at most ``MAX_HOSTS`` per
network) and reads the state of charge where the port is open, like the
connection test. A battery whose single Modbus connection is held by another
integration does not answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv4Network, ip_network
import logging

from homeassistant.components import network
from homeassistant.core import HomeAssistant

from .const import DEFAULT_MODBUS_PORT, DEFAULT_UNIT_ID
from .drivers.marstek_venus_e3 import MarstekVenusE3Driver

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 0.6
PARALLEL_CONNECTS = 64
# Larger networks are reduced to the /24 around Home Assistant's address.
MAX_HOSTS = 1022


def scan_networks(addresses: Iterable[tuple[str, int]]) -> list[IPv4Network]:
    """Networks to scan from (address, prefix length) of the adapters."""
    result: list[IPv4Network] = []
    for address, prefix in addresses:
        net = ip_network(f"{address}/{prefix}", strict=False)
        if not isinstance(net, IPv4Network) or net.is_loopback or net.is_link_local:
            continue
        if net.num_addresses - 2 > MAX_HOSTS:
            net = ip_network(f"{address}/24", strict=False)
        if net not in result:
            result.append(net)
    return result


async def _port_open(host: str, port: int, semaphore: asyncio.Semaphore) -> bool:
    async with semaphore:
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True


async def async_find_batteries(
    hass: HomeAssistant, exclude_hosts: Iterable[str] = ()
) -> list[tuple[str, float]]:
    """(IP address, state of charge) of every Venus found, sorted by address."""
    adapters = await network.async_get_adapters(hass)
    networks = scan_networks(
        (ip["address"], ip["network_prefix"])
        for adapter in adapters
        if adapter["enabled"]
        for ip in adapter["ipv4"]
    )
    own = {str(ip) for ip in await network.async_get_enabled_source_ips(hass)}
    skip = own | set(exclude_hosts)
    hosts = [str(host) for net in networks for host in net.hosts() if str(host) not in skip]
    semaphore = asyncio.Semaphore(PARALLEL_CONNECTS)
    open_ports = await asyncio.gather(
        *(_port_open(host, DEFAULT_MODBUS_PORT, semaphore) for host in hosts)
    )
    found: list[tuple[str, float]] = []
    for host, is_open in zip(hosts, open_ports, strict=True):
        if not is_open:
            continue
        soc = await MarstekVenusE3Driver.read_soc(host, DEFAULT_MODBUS_PORT, DEFAULT_UNIT_ID)
        if soc is not None:
            found.append((host, soc))
    _LOGGER.debug("Battery search: %d hosts, %d open, found %s", len(hosts), sum(open_ports), found)
    return sorted(found, key=lambda item: IPv4Address(item[0]))
