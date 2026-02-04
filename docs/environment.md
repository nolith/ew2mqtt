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

# Format Nix files
nix fmt
```

## Environment Variables

| Variable          | Default         | Description                  |
|-------------------|-----------------|------------------------------|
| `WALLBOX_ADDRESS` | `demo`          | BLE MAC address of wallbox   |
| `WALLBOX_PIN`     | `9844`          | BLE authentication PIN       |
| `WALLBOX_TOPIC`   | `easywallbox`   | MQTT topic prefix            |
| `MQTT_HOST`       | `192.168.2.70`  | MQTT broker hostname         |
| `MQTT_PORT`       | `1883`          | MQTT broker port             |
| `MQTT_USERNAME`   | (empty)         | MQTT authentication username |
| `MQTT_PASSWORD`   | (empty)         | MQTT authentication password |

### Demo Mode

When `WALLBOX_ADDRESS=demo`, the application runs without a real BLE device.
This is useful for testing MQTT integration.
