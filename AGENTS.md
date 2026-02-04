# AGENTS.md

Python BLE-to-MQTT bridge for EasyWallbox EV chargers (Python 3.12+, asyncio).

## Setup

Uses **Nix flakes** with direnv: `direnv allow` or `nix develop`

## Run

- `easywallbox-mqtt` - Main MQTT bridge daemon
- `easywallbox-terminal [ADDR]` - Interactive BLE terminal
- `ble_scan` - Scan for BLE devices

## Documentation

- [Code Style](docs/code-style.md) - Naming, async patterns, logging
- [Architecture](docs/architecture.md) - BLE state machine, components, MQTT topics
- [Environment](docs/environment.md) - Environment variables, Nix setup
