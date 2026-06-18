"""AC packet builder and response parser (Velit Air Conditioner Protocol V1.01).

Packet structure (both directions):
  [0x5A][0x5A][length][product_code][func][data N][checksum 1B][0x0D][0x0A]

Checksum: sum of all bytes from frame header through data, low byte only.
Verified against the single known example in the protocol document.

Length field: based on the single documented example (1 data byte, length=6),
the length value equals len(data) + 5. Theory — requires hardware verification
with multi-byte payloads.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

# Packet framing.
HEADER = bytes([0x5A, 0x5A])
TERMINATOR = bytes([0x0D, 0x0A])

# Product code used in the documented example. The protocol does not document
# all valid product code values — needs verification.
DEFAULT_PRODUCT_CODE = 0x01

# Minimum valid response: header(2) + len(1) + product(1) + func(1) + data(>=1) + cksum(1) + term(2)
_MIN_RESPONSE_LEN = 9


def build_command(func: int, data: bytes, product_code: int = DEFAULT_PRODUCT_CODE) -> bytes:
    """Build a complete AC command packet ready to write to BLE."""
    # Length theory: len(data) + 5, based on the single documented example
    # (1 data byte, length=6).
    length = len(data) + 5
    payload = HEADER + bytes([length, product_code, func]) + data
    return payload + bytes([checksum(payload)]) + TERMINATOR


def parse_response(raw: bytes) -> dict | None:
    """Parse an AC response packet.

    Validates header, terminator, minimum length, and checksum. Returns a dict
    with keys func, data, product_code, or None if the packet is invalid.
    """
    if len(raw) < _MIN_RESPONSE_LEN:
        _LOGGER.debug("Response too short: %d bytes", len(raw))
        return None

    if raw[:2] != HEADER:
        _LOGGER.debug("Unexpected header: %s", raw[:2].hex())
        return None

    if raw[-2:] != TERMINATOR:
        _LOGGER.debug("Missing terminator: %s", raw[-2:].hex())
        return None

    if not validate_checksum(raw):
        _LOGGER.debug("Checksum mismatch on response: %s", raw.hex())
        return None

    return {
        "func": raw[4],
        "data": raw[5:-3],  # between func byte and checksum+terminator
        "product_code": raw[3],
    }


def checksum(payload: bytes) -> int:
    """Compute the 1-byte checksum (sum of header through data, low byte only)."""
    return sum(payload) & 0xFF


def validate_checksum(raw: bytes) -> bool:
    """Return True if the response checksum byte (raw[-3]) is correct.

    Checksum covers all bytes from header through data (raw[:-3]), excluding the
    checksum byte and the 0x0D 0x0A terminator.
    """
    return raw[-3] == checksum(raw[:-3])
