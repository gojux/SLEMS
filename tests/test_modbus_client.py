"""Tests for Modbus register decoding and the v3 frame fix."""

from custom_components.slems.drivers.modbus_client import (
    decode_registers,
    fix_v3_exception_frame,
)


def test_decode_signed_and_unsigned() -> None:
    assert decode_registers([0xFFFF], "int16") == -1
    assert decode_registers([0xFFFF], "uint16") == 0xFFFF
    assert decode_registers([0x0001, 0x0000], "uint32") == 65536
    assert decode_registers([0xFFFF, 0xFFFE], "int32") == -2
    assert decode_registers([0x0001], "int32") is None


def test_decode_char() -> None:
    assert decode_registers([0x5645, 0x4E55, 0x5300], "char") == "VENUS"


def test_v3_exception_frame_is_patched() -> None:
    broken = bytes([0, 1, 0, 0, 0, 4, 1, 0x83, 2])
    assert fix_v3_exception_frame(False, broken)[5] == 3
    # Outgoing frames and regular responses stay untouched.
    assert fix_v3_exception_frame(True, broken) == broken
    regular = bytes([0, 1, 0, 0, 0, 4, 1, 0x03, 2])
    assert fix_v3_exception_frame(False, regular) == regular
