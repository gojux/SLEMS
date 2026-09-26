"""Tests for the networks searched for batteries."""

from ipaddress import ip_network

from custom_components.slems.discovery import scan_networks


def test_networks_of_the_adapters() -> None:
    networks = scan_networks([("192.168.0.20", 24), ("127.0.0.1", 8), ("169.254.3.4", 16)])
    assert networks == [ip_network("192.168.0.0/24")]


def test_large_networks_are_reduced_to_the_own_24() -> None:
    assert scan_networks([("172.18.5.3", 16)]) == [ip_network("172.18.5.0/24")]
    # Up to 1022 hosts are searched as they are.
    assert scan_networks([("10.0.1.7", 22)]) == [ip_network("10.0.0.0/22")]


def test_duplicates_are_removed() -> None:
    assert scan_networks([("192.168.0.20", 24), ("192.168.0.21", 24)]) == [
        ip_network("192.168.0.0/24")
    ]
