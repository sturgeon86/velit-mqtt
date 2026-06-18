"""Unit tests for the heater and AC wire protocols.

Test vectors are taken directly from the Velit protocol documents
(Heater V1.02, AC V1.01) and used as ground truth — carried over from the
original integration's packet tests.
"""

from __future__ import annotations

import pytest

from velit_mqtt.protocol import ac as ac_protocol
from velit_mqtt.protocol import heater as heater_protocol

MASTER = bytes([0x00, 0x00, 0x00, 0x01])
SLAVE = bytes([0x00, 0x00, 0x00, 0x2D])


class TestHeaterBuildCommand:
    def _cmd(self, func: int, data: int) -> bytes:
        return heater_protocol.build_command(MASTER, SLAVE, func, bytes([data]))

    def test_start_flag(self):
        assert self._cmd(0x01, 0x01)[0] == 0x55

    def test_length_field(self):
        # master(4) + slave(4) + func(1) + data(1) + checksum(2) = 12 = 0x0C
        assert self._cmd(0x01, 0x01)[1] == 0x0C

    @pytest.mark.parametrize(
        "func, data, expected",
        [
            (0x00, 0x01, bytes([0x00, 0x90])),  # mode switch manual
            (0x00, 0x02, bytes([0x00, 0x91])),  # mode switch thermostat
            (0x01, 0x01, bytes([0x00, 0x91])),  # startup manual
            (0x03, 0x00, bytes([0x00, 0x92])),  # start ventilation
            (0x04, 0x00, bytes([0x00, 0x93])),  # stop ventilation
            (0x05, 0x00, bytes([0x00, 0x94])),  # start fuel pump
            (0x06, 0x00, bytes([0x00, 0x95])),  # stop fuel pump
            (0x07, 0x03, bytes([0x00, 0x99])),  # set gear 3
            (0x08, 0x50, bytes([0x00, 0xE7])),  # set temperature 80F
            (0x09, 0x00, bytes([0x00, 0x98])),  # residual fuel clearance
        ],
    )
    def test_checksums(self, func, data, expected):
        assert self._cmd(func, data)[-2:] == expected

    def test_invalid_address_length(self):
        with pytest.raises(ValueError):
            heater_protocol.build_command(bytes([0x01]), SLAVE, 0x01, bytes([0x01]))

    def test_address_embedded_correctly(self):
        pkt = self._cmd(0x01, 0x01)
        assert pkt[2:6] == MASTER
        assert pkt[6:10] == SLAVE


class TestHeaterParseResponse:
    def _rsp(self, func: int, data: int) -> bytes:
        base = bytes([0xAA, 0x0E]) + MASTER + SLAVE + bytes([0x53, 0x46, func, data])
        total = sum(base) & 0xFFFF
        return base + bytes([total >> 8, total & 0xFF])

    def test_startup_response(self):
        result = heater_protocol.parse_response(self._rsp(0x01, 0x01))
        assert result is not None
        assert result["func"] == 0x01
        assert result["data"] == bytes([0x01])

    def test_addresses_returned(self):
        result = heater_protocol.parse_response(self._rsp(0x01, 0x01))
        assert result["master_addr"] == MASTER
        assert result["slave_addr"] == SLAVE

    def test_invalid_start_byte(self):
        raw = bytes([0x55]) + self._rsp(0x01, 0x01)[1:]
        assert heater_protocol.parse_response(raw) is None

    def test_bad_manufacturer_code(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[10] = 0xFF
        assert heater_protocol.parse_response(bytes(raw)) is None

    def test_bad_checksum(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[-1] ^= 0xFF
        assert heater_protocol.parse_response(bytes(raw)) is None

    def test_too_short(self):
        assert heater_protocol.parse_response(bytes([0xAA, 0x01])) is None


class TestACBuildCommand:
    def test_known_example(self):
        # Protocol example: 5A5A 06 01 01 01 BD 0D0A
        pkt = ac_protocol.build_command(func=0x01, data=bytes([0x01]), product_code=0x01)
        assert pkt == bytes([0x5A, 0x5A, 0x06, 0x01, 0x01, 0x01, 0xBD, 0x0D, 0x0A])

    def test_header_and_terminator(self):
        pkt = ac_protocol.build_command(func=0x01, data=bytes([0x01]))
        assert pkt[:2] == bytes([0x5A, 0x5A])
        assert pkt[-2:] == bytes([0x0D, 0x0A])

    def test_checksum_position(self):
        pkt = ac_protocol.build_command(func=0x01, data=bytes([0x01]))
        assert pkt[-3] == 0xBD


class TestACParseResponse:
    def _rsp(self, func: int, data: int, product_code: int = 0x01) -> bytes:
        payload = bytes([0x5A, 0x5A, 0x06, product_code, func, data])
        return payload + bytes([sum(payload) & 0xFF, 0x0D, 0x0A])

    def test_known_example(self):
        raw = bytes([0x5A, 0x5A, 0x06, 0x01, 0x01, 0x01, 0xBD, 0x0D, 0x0A])
        result = ac_protocol.parse_response(raw)
        assert result is not None
        assert result["func"] == 0x01
        assert result["data"] == bytes([0x01])

    def test_data_extracted(self):
        result = ac_protocol.parse_response(self._rsp(0x03, 0x19))  # 0x19 = 25
        assert result["data"] == bytes([0x19])

    def test_bad_header(self):
        raw = self._rsp(0x01, 0x01)
        assert ac_protocol.parse_response(bytes([0x55]) + raw[1:]) is None

    def test_bad_terminator(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[-1] = 0xFF
        assert ac_protocol.parse_response(bytes(raw)) is None

    def test_bad_checksum(self):
        raw = bytearray(self._rsp(0x01, 0x01))
        raw[-3] ^= 0xFF
        assert ac_protocol.parse_response(bytes(raw)) is None

    def test_too_short(self):
        assert ac_protocol.parse_response(bytes([0x5A, 0x5A])) is None
