# Environment Setup

## Nix Flakes

This project uses Nix flakes for reproducible development environments.

### With direnv (recommended)

```bash
# Allow direnv to auto-load the environment
direnv allow
```

### Manual Commands

```bash
# Enter development shell
nix develop

# Build the package
nix build

# Format Nix and Python files (treefmt, configured in treefmt.toml)
nix fmt
```

## Environment Variables

| Variable                | Default        | Description                  |
|-------------------------|----------------|------------------------------|
| `WALLBOX_ADDRESS`       | `demo`         | BLE MAC address of wallbox   |
| `WALLBOX_PIN`           | `9844`         | BLE authentication PIN       |
| `WALLBOX_TOPIC`         | `easywallbox`  | MQTT topic prefix            |
| `MQTT_HOST`             | `192.168.2.70` | MQTT broker hostname         |
| `MQTT_PORT`             | `1883`         | MQTT broker port             |
| `MQTT_USERNAME`         | (empty)        | MQTT authentication username |
| `MQTT_PASSWORD`         | (empty)        | MQTT authentication password |
| `STANDALONE`            | `false`        | Start in standalone mode     |
| `STANDALONE_DPM_LIMIT`  | (none)         | Standalone DPM limit, A      |
| `STANDALONE_USER_LIMIT` | (none)         | Standalone user limit, A     |

### Demo Mode

When `WALLBOX_ADDRESS=demo`, the application runs without a real BLE device.
This is useful for testing MQTT integration.

## systemd

The bridge speaks the `sd_notify` protocol through `$NOTIFY_SOCKET` using only
the standard library (`sd_notify.py`). Without `NOTIFY_SOCKET` every
notification is a no-op.

- `READY=1` once MQTT is connected, without waiting for the wallbox
- `WATCHDOG=1` every `WatchdogSec / 2`, only while the bridge makes progress:
  data from the wallbox, or a running reconnect loop, within the last
  `PROGRESS_TIMEOUT_SEC` (90s, or 4 poll intervals if longer)
- `STATUS=` on every BLE state change and watchdog check
- `STOPPING=1` when shutdown starts

```ini
[Service]
Type=notify
NotifyAccess=main
WatchdogSec=30s
Restart=on-failure
RestartSec=10s
```
