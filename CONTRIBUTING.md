# Contributing to velit-mqtt

A standalone BLE-to-MQTT bridge for Velit camping heaters and air conditioners.
Community project — all skill levels welcome.

## Branching & Pull Requests

- Do not develop directly on `main` or `dev`
- Create a feature branch from `dev` for each piece of work: `feature/short-description`
- Bug fixes branch as: `fix/short-description`
- Open PRs targeting `dev`; `main` is reserved for releases
- Keep PRs small and focused — one concern per PR makes review faster
- Write a clear PR description explaining what changed and why

## Hardware Validation

Some work can be fully verified with unit tests alone (packet builders, config parsing,
device state mapping). Work that touches live BLE communication requires validation
against real hardware before it can be merged.

If your PR includes hardware-dependent code, note in the PR description which device you
tested against. If you don't have the hardware, mark the PR clearly so another contributor
can pick up validation.

## Commit Style

- Use the imperative mood: "Add temperature conversion" not "Added" or "Adding"
- Keep the subject line concise — under 72 characters
- If more context is needed, add it in the body after a blank line
- No filler phrases, no commentary on how the code was written

Examples of good commit messages:
```
Add heater packet builder and LRC2 checksum
Fix device poll backoff on BLE timeout
Remove unused import in the MQTT bridge
```

## Code Standards

- Python 3.11+, full type hints throughout
- Lint with `ruff`
- No bare `except:` — catch specific exception types
- All I/O must be async — no blocking calls on the event loop
- Non-trivial logic must be commented; comments explain *why*, not *what*
- No emojis in code, comments, commits, or documentation

## Project Layout & Conventions

- `velit_mqtt/protocol/` — pure packet build/parse; no I/O, fully unit-tested
- `velit_mqtt/ble.py` — BLE transport (bleak); the device poll loop owns reconnection
- `velit_mqtt/devices.py` — poll, parse, public state mapping, and commands
- `velit_mqtt/mqtt_bridge.py` / `discovery.py` — MQTT publish/subscribe and HA discovery
- Keep the public MQTT state schema and `set/<field>` topics stable; document changes in the README
- Generic topics are the source of truth — HA discovery payloads reference them, they do not duplicate state

## Testing

See [TESTING.md](TESTING.md) for unit test setup, hardware validation checklists,
and debug logging guidance.

## Questions & Discussion

Open a GitHub issue or start a discussion on the repository. PRs are also a good place
to discuss approach before writing code — a draft PR with a description is a valid way
to propose a plan.
