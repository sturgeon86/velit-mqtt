"""Temperature conversion and unit-detection helpers.

The bridge preserves whatever unit is currently active on the device's physical
LCD (it never sends a value that would flip the display). On connect it reads
the current setpoint and infers the unit from its range; all SET commands and
sensor decodes then use that unit, and values are converted to Celsius before
being published so consumers see a single consistent unit.

Two distinct encodings exist in the protocol:

  SET commands (heater 0x08 / AC 0x03):
    The data byte is the raw integer degree value in the active unit — no offset.

  Heater sensor readings (Query 2, func 0x0B):
    Offset encoding verified on hardware:
      Celsius mode:    raw - 50 = °C
      Fahrenheit mode: raw - 60 = °F
"""

from __future__ import annotations

from .const import (
    CELSIUS_SETPOINT_MAX,
    CELSIUS_SETPOINT_MIN,
    FAHRENHEIT_SETPOINT_MAX,
    FAHRENHEIT_SETPOINT_MIN,
)

UNIT_CELSIUS = "C"
UNIT_FAHRENHEIT = "F"


def celsius_to_fahrenheit(temp_c: float) -> float:
    """Convert Celsius to Fahrenheit using the protocol formula."""
    return 1.8 * temp_c + 32


def fahrenheit_to_celsius(temp_f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (temp_f - 32) / 1.8


def detect_unit(setpoint: int, fallback: str = UNIT_CELSIUS) -> str:
    """Infer the device's display unit from a current setpoint value.

    Returns UNIT_CELSIUS or UNIT_FAHRENHEIT. Falls back to ``fallback`` when the
    setpoint is outside both known ranges (e.g. on first boot or after a fault
    reset where the value is 0x00).
    """
    if CELSIUS_SETPOINT_MIN <= setpoint <= CELSIUS_SETPOINT_MAX:
        return UNIT_CELSIUS
    if FAHRENHEIT_SETPOINT_MIN <= setpoint <= FAHRENHEIT_SETPOINT_MAX:
        return UNIT_FAHRENHEIT
    return fallback


def native_to_celsius(value: float, unit: str) -> float:
    """Convert a value already in the device's active unit to Celsius."""
    if unit == UNIT_FAHRENHEIT:
        return fahrenheit_to_celsius(value)
    return float(value)


def celsius_to_native(temp_c: float, unit: str) -> int:
    """Convert a Celsius setpoint to the rounded integer value to transmit.

    Sends in the device's active unit so the physical LCD display unit is
    never flipped.
    """
    if unit == UNIT_FAHRENHEIT:
        return round(celsius_to_fahrenheit(temp_c))
    return round(temp_c)


def decode_sensor_temp(raw: int, unit: str) -> float | None:
    """Decode a raw heater Query 2 sensor temperature to native degrees.

    Applies the protocol offset (raw - 50 for °C, raw - 60 for °F). Returns
    None for the 0xFFFF unavailable sentinel.
    """
    if raw == 0xFFFF:
        return None
    if unit == UNIT_FAHRENHEIT:
        return float(raw - 60)
    return float(raw - 50)
