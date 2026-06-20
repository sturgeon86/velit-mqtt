# velit-mqtt

Bridge Velit camping heaters and air conditioners to **MQTT** over Bluetooth.

`velit-mqtt` is a standalone service: it connects to your Velit device over
Bluetooth Low Energy, polls its state, and publishes it to an MQTT broker —
while accepting commands back on MQTT. It works with any MQTT consumer, and can
optionally publish Home Assistant discovery messages so the device appears in HA
automatically (no custom component required).

> This project is a fork of [**velit-hass**](https://github.com/JohnFreeborg/velit-hass),
> the Home Assistant integration created by [John Freeborg](https://github.com/JohnFreeborg)
> and contributors. It has been re-architected as a broker-agnostic MQTT service: the
> validated BLE protocol layer is carried over from the original, and the Home Assistant
> runtime is replaced. Full credit for the original Velit protocol work goes to the
> upstream authors — see [Credits](#credits).

---

> [!WARNING]
> **Read this before proceeding.**
>
> This project involves interfacing with a **combustion heater** that produces **open flame, high heat, and carbon monoxide**. Improper installation, software faults, or loss of communication between the controller and heater can result in **fire, carbon monoxide poisoning, serious injury, or death**.
>
> **By using any part of this project — code, documentation, or captures — you accept full and sole responsibility for your implementation, installation, and any consequences that result.** The author(s) of this project provide it as-is, with no warranty of any kind, expressed or implied. This project is not affiliated with VELIT Cooling & Heating, LLC or any related entity.
>
> **Minimum precautions you should take:**
> - Install a working CO detector in any enclosed space where the heater operates
> - Never leave a combustion heater running unattended without independent safety mechanisms (CO detector, thermal cutoff, smoke alarm)
> - Test all control and shutdown paths thoroughly before relying on this system
> - Retain the ability to cut heater power independently of this controller at all times
> - Consult a qualified installer if you are uncertain about any aspect of the wiring or installation

---

## Supported Devices

| Device | Type | Tested | Firmware |
|---|---|---|---|
| Velit 4000P (fixed) | Heater | Yes | 3.13 / 3.26 / 3.62 |
| Velit Portable | Heater | No | — |
| Velit 2000R | AC | In progress | — |
| Velit 2000R Mini | AC | No | — |
| Velit 3000R | AC | No | — |
| Velit 2000U | AC | No | — |
| Velit V3 | AC | No | — |

If you have tested this on a device not listed above, please open an issue with
the model and firmware version so the table can be updated.

---

## Features

- Built-in web UI (localhost) to scan for devices, add/remove them, and edit MQTT broker settings — applied live, no restart
- Connects to one or more Velit devices over BLE and keeps them connected
- Publishes a full JSON state object per device (retained) and routes commands back
- Adaptive polling — drops to 5 s during state transitions and after commands, returns to the configured interval once settled
- Per-device availability plus a bridge-level last-will, so consumers can tell "bridge down" from "device unreachable"
- Optional Home Assistant MQTT discovery (climate, sensors, switches) pointing at the same generic topics
- Preserves the device's physical °C/°F display unit; publishes everything in °C
- Auto-reconnect with backoff; release the BLE link on demand to hand the device to the Velit mobile app

**Heater:** power, manual/thermostat mode, gear 1–5, target temperature, inlet
temperature, altitude, machine state, fault code + fault-active flag, voltage,
fan RPM, heater power, fuel-pump prime cycle (30 s auto-stop) with live
countdown, residual-fuel cleaning cycle, firmware version.

**Air Conditioner:** power, modes (cool, fan-only), presets (Eco, Sleep, Turbo),
fan speed 1–5, target temperature, inlet temperature, fault code.

---

## Requirements

- A Linux host with a working Bluetooth adapter and BlueZ (e.g. a Raspberry Pi)
- Python 3.11+
- An MQTT broker (e.g. Mosquitto) reachable from the host
- A Velit heater or air conditioner with Bluetooth enabled

> BLE allows only one connection at a time. Close the Velit mobile app on nearby
> phones while the bridge is running, or it will compete for the connection.

---

## Installation

```bash
sudo install -d -o "$USER" /opt/velit-mqtt
git clone https://github.com/JohnFreeborg/velit-hass /opt/velit-mqtt
cd /opt/velit-mqtt

python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Create the config (you can leave `devices` empty and add them from the web UI):

```bash
sudo install -d /etc/velit-mqtt
sudo cp config.example.yaml /etc/velit-mqtt/config.yaml
sudo nano /etc/velit-mqtt/config.yaml      # set your broker host/credentials
```

Run it in the foreground to check everything connects:

```bash
./venv/bin/python -m velit_mqtt --config /etc/velit-mqtt/config.yaml
```

Then open the web UI at **http://127.0.0.1:8099** to scan for and add your
device(s) — or add them by hand in the config file. To find an address from the
command line instead, use `./venv/bin/python tools/discover.py` (close the Velit
mobile app first).

### Run as a service

```bash
sudo cp systemd/velit-mqtt.service /etc/systemd/system/
sudo nano /etc/systemd/system/velit-mqtt.service   # check User/paths
sudo systemctl daemon-reload
sudo systemctl enable --now velit-mqtt
journalctl -u velit-mqtt -f
```

### Raspberry Pi (Bookworm)

This runs comfortably on a Raspberry Pi 3B or newer — the load is tiny (well
under ~80 MB RAM, near-idle CPU). Use **64-bit Raspberry Pi OS Bookworm**, which
ships Python 3.11; the ARM wheels for all dependencies install automatically via
piwheels, so nothing needs compiling. Pi-specific steps:

- **Bluetooth access.** The service user must be in the `bluetooth` group to
  reach BlueZ over D-Bus, and the radio must be unblocked:
  ```bash
  sudo usermod -aG bluetooth pi      # use your actual user
  rfkill unblock bluetooth
  ```
  Set `User=pi` (your user) in the systemd unit, then log out/in (or reboot) so
  the new group membership takes effect.

- **Range.** The Pi's onboard Bluetooth shares the 2.4 GHz antenna with Wi-Fi
  and is fairly weak. Keep the Pi within good range of the device; if it sits
  behind walls or across a camper, a cheap USB BLE dongle improves reliability.

- **Multiple devices.** Each Velit device uses one BLE connection, so running a
  heater and an AC at once means two simultaneous links — fine for the Pi, but
  worth confirming on your hardware.

- **Web UI.** It binds to `127.0.0.1` by default. To reach it from your laptop,
  either tunnel (`ssh -L 8099:localhost:8099 pi@<pi>`) or set `web.host: 0.0.0.0`
  **with** a `web.token`.

---

## Configuration

See [`config.example.yaml`](config.example.yaml) for the full annotated file.

```yaml
mqtt:
  host: localhost
  port: 1883
  # username / password (or password_env: VELIT_MQTT_PASSWORD)
  base_topic: velit
  discovery: true            # publish Home Assistant discovery configs
  discovery_prefix: homeassistant

devices:
  - name: Camper Heater      # node id is derived from the name (camper_heater)
    address: "AA:BB:CC:DD:EE:FF"
    type: heater             # heater | ac
    poll_interval: 30
```

The config path is taken from `--config`, else `$VELIT_MQTT_CONFIG`, else
`./config.yaml`, else `/etc/velit-mqtt/config.yaml`.

---

## Web UI

A small management UI is served at **http://`web.host`:`web.port`** (default
`http://127.0.0.1:8099`). It lets you:

- **Scan** for nearby Velit devices over BLE and **add** them (name + type) — written to the config and started live
- **Remove** devices
- Edit the **MQTT broker** settings (host, port, credentials, base topic, discovery) — applied with a live reconnect

Changes are persisted back to the config file, so they survive restarts.

> The UI binds to localhost by default. It can add devices and change broker
> settings on a service that controls a combustion heater, so only expose it
> beyond localhost (`host: 0.0.0.0`) if you also set a `token` — it is then
> required as an `X-Auth-Token` header or `?token=` query parameter
> (`http://host:8099/?token=...`). Disable it entirely with `web.enabled: false`.

---

## MQTT interface

Each device gets a **node id** derived from its name (e.g. `camper_heater`).
With the default `base_topic: velit`:

| Topic | Direction | Payload |
|---|---|---|
| `velit/<node>/state` | published, retained | full JSON state (below) |
| `velit/<node>/availability` | published, retained | `online` / `offline` (BLE reachability) |
| `velit/bridge/availability` | published, retained | `online` / `offline` (bridge liveness; MQTT LWT) |
| `velit/<node>/set/<field>` | subscribed | command value |

### Commands (`velit/<node>/set/<field>`)

| Field | Devices | Payload |
|---|---|---|
| `mode` | both | heater: `off`, `heat` · AC: `off`, `cool`, `fan_only` |
| `preset` | both | heater: `Auto`, `Manual` · AC: `Cooling`, `Eco`, `Sleep`, `Turbo` |
| `temperature` | both | number, in °C (sent to the device in its active unit) |
| `fan` | both | `1`–`5` |
| `ble` | both | `ON` (connect) / `OFF` (release for the mobile app) |
| `prime` | heater | `ON` (start 30 s prime) / `OFF` (stop early) |
| `cleaning` | heater | `ON` (start residual-fuel cleaning) |

Example:

```bash
mosquitto_pub -t velit/camper_heater/set/mode -m heat
mosquitto_pub -t velit/camper_heater/set/temperature -m 21
```

### State payload

Heater `velit/<node>/state`:

```json
{
  "power": "on", "mode": "heat", "preset": "Auto", "action": "heating",
  "target_temperature": 21, "current_temperature": 19.0, "fan": "3",
  "machine_state": "Normal", "fault_code": 0, "fault": "No Fault",
  "fault_active": false, "altitude": 120, "voltage": 13.2, "fan_rpm": 2400,
  "heater_power_w": 1200, "fuel_pump_hz": 2.5, "temp_unit": "C",
  "firmware": "3.26", "priming": false, "prime_remaining": 0, "cleaning": false
}
```

AC `velit/<node>/state`:

```json
{
  "power": "on", "mode": "cool", "preset": "Cooling", "action": "cooling",
  "target_temperature": 24, "current_temperature": 24.0, "fan": "3",
  "fault_code": 0, "fault": "No Fault", "temp_unit": "C"
}
```

---

## Home Assistant (via MQTT discovery)

With `discovery: true` and the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/)
configured in Home Assistant, each device appears automatically as a climate
entity plus sensors and switches — no custom component needed. Discovery configs
point at the generic topics above, so the same setup works for non-HA consumers.

Set `discovery: false` for a plain, broker-agnostic interface.

---

## Troubleshooting

**Device not found / keeps disconnecting**
- Close the Velit mobile app on all nearby phones — it holds the single BLE connection.
- Confirm the device is powered on and in range; check `bluetoothctl` sees it.
- Ensure the service user can access BlueZ over D-Bus (the unit uses group `bluetooth`).

**Nothing on MQTT**
- Check `journalctl -u velit-mqtt -f` for connection errors.
- Verify broker host/port/credentials; watch `velit/bridge/availability`.

**Heater takes a long time to turn off**
- After a heat cycle the heater runs a ~3 minute cool-down before shutting off
  completely. `action` reports `fan` during this period. This is normal device behaviour.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branching, commit, and testing guidelines.

---

## Credits

This project is a fork of **[velit-hass](https://github.com/JohnFreeborg/velit-hass)**
by **[John Freeborg](https://github.com/JohnFreeborg)** and contributors (including
[Jeremy Roe](https://github.com/jeremyroe)). The original project reverse-engineered the
Velit Bluetooth heater (V1.02) and air-conditioner (V1.01) protocols and built the Home
Assistant integration that this service's BLE and protocol layer is derived from. Huge
thanks for that foundational work.

This fork re-architects the integration into a standalone BLE-to-MQTT service. It is an
independent project and is not affiliated with or endorsed by the original authors, nor
by VELIT Cooling &amp; Heating, LLC.
