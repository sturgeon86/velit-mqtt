"""Heater packet builder and response parser (Velit Air Heater Protocol V1.02).

Packet structure (command, master to slave):
  [0x55][length][master 4B][slave 4B][func][data N][checksum 2B]

Packet structure (response, slave to master):
  [0xAA][length][master 4B][slave 4B][SF 2B][func][data N][checksum 2B]

Checksum: unsigned 16-bit sum of all bytes from start flag through data,
high byte first. Verified against 11 of 12 protocol examples — the shutdown
command example (func 0x02) appears to contain a typo in the source document.

Addressing: slave address 0x0000002D is a fixed constant on all known Velit
heaters (verified on hardware, 2026-03-25). Master address is arbitrary.
Device isolation is provided by the BLE connection, not protocol addressing.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Packet framing constants.
START_CMD = 0x55
START_RSP = 0xAA
MFG_CODE = bytes([0x53, 0x46])  # "SF" — present in every response

# Minimum valid response: AA + len + master(4) + slave(4) + SF(2) + func + data(>=1) + cksum(2)
_MIN_RESPONSE_LEN = 16


def build_command(
    master_addr: bytes,
    slave_addr: bytes,
    func: int,
    data: bytes,
) -> bytes:
    """Build a complete heater command packet ready to write to BLE.

    Raises ValueError if either address is not exactly 4 bytes.
    """
    if len(master_addr) != 4 or len(slave_addr) != 4:
        raise ValueError("master_addr and slave_addr must each be 4 bytes")

    # Length = bytes after the length field: master(4) + slave(4) + func(1) + data(N) + checksum(2)
    length = 4 + 4 + 1 + len(data) + 2

    header = bytes([START_CMD, length]) + master_addr + slave_addr + bytes([func]) + data
    return header + checksum(header)


def parse_response(raw: bytes) -> dict | None:
    """Parse a heater response packet.

    Validates start flag, minimum length, manufacturer code, and checksum.
    Returns a dict with keys func, data, master_addr, slave_addr, or None if
    the packet is invalid.
    """
    if len(raw) < _MIN_RESPONSE_LEN:
        _LOGGER.debug("Response too short: %d bytes", len(raw))
        return None

    if raw[0] != START_RSP:
        _LOGGER.debug("Unexpected start byte: 0x%02X", raw[0])
        return None

    # Manufacturer code at bytes 10–11 (after AA + len + master(4) + slave(4)).
    if raw[10:12] != MFG_CODE:
        _LOGGER.debug("Manufacturer code mismatch: %s", raw[10:12].hex())
        return None

    if not validate_checksum(raw):
        _LOGGER.debug("Checksum mismatch on response: %s", raw.hex())
        return None

    return {
        "func": raw[12],
        "data": raw[13:-2],  # between func byte and the 2-byte trailing checksum
        "master_addr": raw[2:6],
        "slave_addr": raw[6:10],
    }


def checksum(payload: bytes) -> bytes:
    """Compute the 2-byte checksum for a heater packet.

    Sum of all bytes in payload (start flag through data), returned as 2 bytes
    big-endian (high byte first).
    """
    total = sum(payload) & 0xFFFF
    return bytes([total >> 8, total & 0xFF])


def validate_checksum(raw: bytes) -> bool:
    """Return True if the response checksum (final 2 bytes) is correct."""
    return raw[-2:] == checksum(raw[:-2])
