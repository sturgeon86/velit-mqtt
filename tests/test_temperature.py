"""Unit tests for temperature conversion and unit detection."""

from __future__ import annotations

import pytest

from velit_mqtt.temperature import (
    UNIT_CELSIUS,
    UNIT_FAHRENHEIT,
    celsius_to_fahrenheit,
    celsius_to_native,
    decode_sensor_temp,
    detect_unit,
    fahrenheit_to_celsius,
    native_to_celsius,
)


def test_celsius_to_fahrenheit():
    assert celsius_to_fahrenheit(4) == pytest.approx(39.2)
    assert celsius_to_fahrenheit(37) == pytest.approx(98.6)


def test_fahrenheit_to_celsius():
    assert fahrenheit_to_celsius(98.6) == pytest.approx(37.0)
    assert fahrenheit_to_celsius(39.2) == pytest.approx(4.0)


@pytest.mark.parametrize(
    "setpoint, expected",
    [
        (4, UNIT_CELSIUS),
        (21, UNIT_CELSIUS),
        (37, UNIT_CELSIUS),
        (40, UNIT_FAHRENHEIT),
        (72, UNIT_FAHRENHEIT),
        (99, UNIT_FAHRENHEIT),
    ],
)
def test_detect_unit(setpoint, expected):
    assert detect_unit(setpoint) == expected


def test_detect_unit_fallback():
    # 0 is outside both ranges — honour the supplied fallback.
    assert detect_unit(0, fallback=UNIT_FAHRENHEIT) == UNIT_FAHRENHEIT
    assert detect_unit(0) == UNIT_CELSIUS


def test_native_to_celsius():
    assert native_to_celsius(21, UNIT_CELSIUS) == 21.0
    assert native_to_celsius(71, UNIT_FAHRENHEIT) == pytest.approx(21.666, abs=0.01)


def test_celsius_to_native():
    # Celsius mode transmits the rounded value unchanged.
    assert celsius_to_native(21.4, UNIT_CELSIUS) == 21
    # Fahrenheit mode transmits the converted, rounded value.
    assert celsius_to_native(20, UNIT_FAHRENHEIT) == 68


def test_decode_sensor_temp():
    # Celsius offset: raw - 50. Fahrenheit offset: raw - 60 (verified on hardware).
    assert decode_sensor_temp(71, UNIT_CELSIUS) == 21
    assert decode_sensor_temp(131, UNIT_FAHRENHEIT) == 71
    # 0xFFFF is the unavailable sentinel.
    assert decode_sensor_temp(0xFFFF, UNIT_CELSIUS) is None
