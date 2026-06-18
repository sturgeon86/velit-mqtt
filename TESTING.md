# Testing Guide

How to run the unit tests and validate against real hardware.

---

## Unit Tests

Unit tests cover packet building and parsing, temperature conversion and unit
detection, device state parsing and command encoding, and config loading. No
Bluetooth hardware is required (the BLE transport is faked).

### Setup

```bash
pip install -r requirements_test.txt
```

### Run

```bash
pytest tests/
```

For coverage:

```bash
pytest tests/ --cov=velit_mqtt --cov-report=term-missing
```

Lint:

```bash
ruff check velit_mqtt tests
```

Tests and lint run automatically on every pull request via GitHub Actions.

---

## Hardware Testing

Some behaviour can only be verified against a real device — BLE connectivity,
command acknowledgement, and sensor readings. If you have a supported device,
work through the relevant checklist and note results in the PR.

### Setup

Run the bridge against a broker (Mosquitto is fine) with the device in range —
see [README.md](README.md). Watch state with:

```bash
mosquitto_sub -v -t 'velit/#'
```

and send commands with `mosquitto_pub` (see the README's MQTT interface table).

### Heater Checklist

The heater should be powered on throughout.

**Connection**
- [ ] Bridge starts without error; `velit/bridge/availability` becomes `online`
- [ ] `velit/<node>/availability` becomes `online`
- [ ] `velit/<node>/state` publishes a full JSON object

**State — verify values are plausible**
- [ ] `current_temperature` matches ambient expectation
- [ ] `voltage` reads approximately 12–13 V
- [ ] `fault` shows "No Fault" / `fault_active` false during normal operation
- [ ] `machine_state` reflects the device (Standby when off, Normal when running)

**Commands — confirm each is acknowledged by the device**
- [ ] `set/mode heat` — device starts up; state `mode` becomes `heat`
- [ ] `set/mode off` — device shuts down
- [ ] `set/temperature <n>` — device acknowledges the new setpoint
- [ ] `set/fan 1`…`5` — device changes gear level
- [ ] `set/preset Auto` / `Manual` — device changes work mode
- [ ] `set/prime ON` — 30 s prime runs, `prime_remaining` counts down, auto-stops
- [ ] `set/cleaning ON` — cleaning cycle runs and clears when complete
- [ ] `set/ble OFF` then `ON` — releases and re-acquires the connection

**Temperature unit**
- [ ] State `temp_unit` matches the device's physical display; the bridge does
  not change the physical display unit, and all values are published in °C

### AC Checklist

**Note: AC hardware testing has not yet been completed. The AC device is
implemented from the protocol spec but unverified against a real unit. Report
all AC results as new findings.**

- [ ] Bridge connects; state publishes
- [ ] `set/mode cool` / `fan_only` / `off` — each acknowledged
- [ ] `set/preset Eco` / `Sleep` / `Turbo` / `Cooling` — each acknowledged
- [ ] `set/fan 1`…`5` — each acknowledged
- [ ] `set/temperature <n>` — acknowledged

**Open questions requiring hardware confirmation**
- [ ] Are Fan mode (0x03) and Vent mode (0x08) functionally different?
- [ ] What does the fault query (0x0B) response look like in normal operation?
- [ ] What does the inlet temperature query (0x07) response look like?

### Reporting Results

Include in your PR: device model and firmware, host/OS, which items passed or
failed, and any unexpected behaviour or log output.

---

## What to Look for in Logs

Set `log_level: DEBUG` in the config (or run with the service logs):

```bash
journalctl -u velit-mqtt -f
```

| Message | Meaning |
|---|---|
| `Connected to <address>` | BLE connection established |
| `detected temp unit C/F` | Unit detection result on first connect |
| `No response to Query 1` | Device did not respond — check range and power |
| `Connection lost to <address>` | Unexpected disconnect — the poll loop will reconnect |
| `Connect failed for <name>` | Reconnect attempt failed; backing off |
| `<name> is now unavailable` | Poll failures exceeded tolerance; availability published offline |
