"""Protocol constants, lookup tables, and command codes for Velit devices.

Everything here is framework-independent — it describes the Velit wire
protocols (heater V1.02, AC V1.01) and is shared by the BLE and device layers.
Values were carried over verbatim from the original Home Assistant integration,
where they were validated against hardware (dates noted inline).
"""

from __future__ import annotations

# Device type identifiers used throughout config and the bridge.
DEVICE_TYPE_HEATER = "heater"
DEVICE_TYPE_AC = "ac"

# BLE characteristic UUIDs — shared across both heater and AC devices.
# Source: Velit communication protocol documents V1.01 / V1.02.
UUID_SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
UUID_READ_NOTIFY = "0000ffe1-0000-1000-8000-00805f9b34fb"
UUID_WRITE = "0000ffe2-0000-1000-8000-00805f9b34fb"

# BLE advertisement filters for discovery scans.
# Name prefixes vary by firmware; the manufacturer ID (BEKEN Corp, 0x585A) and
# the service UUID are more reliable fallbacks. The 0000ffe0 service is a generic
# UART-over-BLE UUID and may match non-Velit devices — the protocol handshake on
# connect is the real guard, so this filter intentionally casts wide.
BLE_NAME_PREFIXES = ("VELIT", "VLIT", "D30", "KT2", "KT")
BLE_MANUFACTURER_ID = 22618

# Heater packet addressing — verified on hardware (2026-03-25, Velit 4000P).
# The slave address 0x0000002D is a fixed constant on all known Velit heaters;
# it does NOT uniquely identify a device. Device isolation is provided entirely
# by the BLE connection. The master address is arbitrary.
HEATER_MASTER_ADDR = bytes([0x00, 0x00, 0x00, 0x01])
HEATER_SLAVE_ADDR = bytes([0x00, 0x00, 0x00, 0x2D])

# AC protocol timing (V1.01).
AC_COMMAND_INTERVAL_MS = 400  # minimum ms between commands per protocol spec
AC_RESPONSE_TIMEOUT_S = 3     # seconds before a command is considered failed
AC_MAX_RETRIES = 3            # attempts before marking device unavailable

# Temperature setpoint ranges (displayed degrees).
# C: 4–37 displayed, 0x04–0x25 transmitted. F: 40–99 displayed.
# Confirmed on firmware 3.26 and 3.8, 2026-04-02.
HEATER_MIN_TEMP_C = 4
HEATER_MAX_TEMP_C = 37
AC_MIN_TEMP_C = 17
AC_MAX_TEMP_C = 30

# ---------------------------------------------------------------------------
# Heater command / query function codes (protocol V1.02)
# ---------------------------------------------------------------------------
HEATER_FUNC_SET_WORK_MODE = 0x00   # data 0x01 = Manual, 0x02 = Thermostat/Auto
HEATER_FUNC_POWER_ON = 0x01        # data 0x01 = Manual, 0x02 = Auto
HEATER_FUNC_POWER_OFF = 0x02       # data 0x00
HEATER_FUNC_PRIME_START = 0x05     # data 0x00
HEATER_FUNC_PRIME_STOP = 0x06      # data 0x00
HEATER_FUNC_SET_GEAR = 0x07        # data 1–5
HEATER_FUNC_SET_TEMP = 0x08        # data raw degrees in device unit
HEATER_FUNC_CLEANING = 0x09        # data 0x00
HEATER_FUNC_QUERY1 = 0x0A          # state query
HEATER_FUNC_QUERY2 = 0x0B          # sensor query
HEATER_FUNC_FIRMWARE = 0x6A        # firmware version query (data 0x01)

# Heater work mode codes (Query 1 response, work_mode field).
HEATER_MODE_MANUAL = 1
HEATER_MODE_THERMOSTAT = 2

# Fuel pump prime cycle duration — matches the physical hardware button auto-stop.
HEATER_PRIME_DURATION_S = 30

# ---------------------------------------------------------------------------
# AC command / query function codes (protocol V1.01)
# ---------------------------------------------------------------------------
AC_FUNC_POWER = 0x01     # query: data 0x00; set: 0x01 = off, 0x02 = on
AC_FUNC_MODE = 0x02      # query: data 0x00; set: mode code
AC_FUNC_TEMP = 0x03      # query: data 0x00; set: raw degrees in device unit
AC_FUNC_FAN = 0x04       # query: data 0x00; set: 1–5
AC_FUNC_INLET = 0x07     # inlet air temperature query
AC_FUNC_FAULT = 0x0B     # fault info query

AC_POWER_OFF = 0x01
AC_POWER_ON = 0x02

# AC operation mode codes (func 0x02).
AC_MODE_COOL = 1
AC_MODE_FAN = 3
AC_MODE_ENERGY_SAVING = 4
AC_MODE_SLEEP = 5
AC_MODE_TURBO = 6
AC_MODE_VENT = 8

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# Heater fault codes from protocol V1.02.
HEATER_FAULT_CODES: dict[int, str] = {
    0: "No Fault",
    1: "Ignition Failure",
    2: "Abnormal Flame Out",
    3: "Voltage Deviation",
    4: "Heat Exchanger Temp Anomaly",
    5: "Ignition Sensor Fault",
    6: "Outlet Temp Sensor Fault",
    7: "Fuel Pump Fault",
    8: "Fan Fault",
    9: "Inlet Temp Sensor Fault",
    10: "Glow Plug Fault",
    11: "Operating Ambient Temp Anomaly",
    12: "Altitude Out of Range",
    13: "Fan Blockage Fault",
    14: "CO Pollution Exceeded",
    15: "LIN Communication Fault",
}

# Heater machine states from Query 1 response (offset 4 of data payload).
HEATER_MACHINE_STATES: dict[int, str] = {
    0: "Standby",
    1: "Normal",
    2: "Cooling Down",
    3: "Overtemp Standby",
    4: "Cleaning",
    5: "Clean Complete",
}

# Machine states that warrant faster polling — device is mid-transition.
# Settled states: 0 (Standby), 1 (Normal).
HEATER_ACTIVE_MACHINE_STATES = frozenset({2, 3, 4, 5})

# Fan / gear levels exposed over MQTT.
FAN_MODES = ["1", "2", "3", "4", "5"]

# Generic HVAC mode strings published on the state topic / accepted on set/mode.
HVAC_MODE_OFF = "off"
HVAC_MODE_HEAT = "heat"
HVAC_MODE_COOL = "cool"
HVAC_MODE_FAN_ONLY = "fan_only"

# Generic HVAC action strings (what the device is actually doing).
HVAC_ACTION_OFF = "off"
HVAC_ACTION_HEATING = "heating"
HVAC_ACTION_COOLING = "cooling"
HVAC_ACTION_FAN = "fan"
HVAC_ACTION_IDLE = "idle"

# Heater preset names (work mode).
HEATER_PRESET_AUTO = "Auto"
HEATER_PRESET_MANUAL = "Manual"
HEATER_PRESETS = [HEATER_PRESET_AUTO, HEATER_PRESET_MANUAL]

# AC preset names. "Cooling" is the no-preset baseline (standard cool mode).
AC_PRESET_NONE = "Cooling"
AC_PRESET_ENERGY_SAVING = "Eco"
AC_PRESET_SLEEP = "Sleep"
AC_PRESET_TURBO = "Turbo"
AC_PRESETS = [AC_PRESET_NONE, AC_PRESET_ENERGY_SAVING, AC_PRESET_SLEEP, AC_PRESET_TURBO]

# Maximum plausible altitude per unit (filters the 0xFFFF sentinel and startup
# garbage). Bounds are deliberately generous (well above Everest).
MAX_ALTITUDE_M = 9000    # metres — above Everest (8849 m)
MAX_ALTITUDE_FT = 30000  # feet   — above Everest (29032 ft)

# Setpoint ranges used to infer Celsius vs Fahrenheit display mode on connect.
# Non-overlapping so the unit can be determined unambiguously.
# Confirmed hardware range: 4–37°C / 40–99°F (firmware 3.26 and 3.8, 2026-04-02).
CELSIUS_SETPOINT_MIN = 4
CELSIUS_SETPOINT_MAX = 37
FAHRENHEIT_SETPOINT_MIN = 40
FAHRENHEIT_SETPOINT_MAX = 99
