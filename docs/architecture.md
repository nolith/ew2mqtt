# Architecture

## BLE State Machine

The `EasyWallbox` class manages connection states:

```
DISCONNECTED -> CONNECTING -> CONNECTED -> PAIRED -> AUTHENTICATED
      ^                                                    |
      |____________________________________________________|
                      (on disconnect/error)
```

States are defined as class constants:
- `BLE_STATE_DISCONNECTED`
- `BLE_STATE_CONNECTING`
- `BLE_STATE_CONNECTED`
- `BLE_STATE_CONNECTED_PAIRED`
- `BLE_STATE_CONNECTED_AUTH`
- `BLE_STATE_TERMINATING`

### Reconnect

`queue_consumer` calls `EasyWallbox.maintain_connection()` once per second.
The link is reset to `DISCONNECTED`, and a new connection is attempted, when:

- bleak's `disconnected_callback` reports a link loss
- the client reports disconnected while the state is still `AUTHENTICATED`
- no data arrives for `STALE_POLL_INTERVALS` (3) poll intervals
- the wallbox does not answer the login within `AUTH_TIMEOUT_SEC` (20s)
- `connect()` fails or takes longer than `CONNECT_TIMEOUT_SEC` (70s): bleak
  gets `BLEAK_CONNECT_TIMEOUT_SEC` (30s) for the scan and again for the
  connect, as the wallbox can advertise only every 6-37s

Failed attempts back off exponentially from `ble_retry_interval` (10s) up to
`RECONNECT_BACKOFF_MAX_SEC` (120s). The first failure of an outage is logged,
then one summary every `OUTAGE_LOG_INTERVAL_SEC` (10 minutes). Commands queued
while the wallbox is disconnected are dropped instead of being replayed later.

## Components

| File                | Purpose                               |
|---------------------|---------------------------------------|
| `easywallbox.py`    | Main application, MQTT-BLE bridge     |
| `blemap.py`         | BLE command/response string constants |
| `commands.py`       | High-level command builders           |
| `mqttmap.py`        | MQTT topic to BLE command mapping     |
| `settings.py`       | Runtime configuration with async access |
| `repeating_timer.py`| Async periodic task executor          |
| `sd_notify.py`      | systemd readiness and watchdog notify |
| `terminal.py`       | Interactive BLE debugging terminal    |
| `scan.py`           | BLE device scanner utility            |
| `readinfo.py`       | GATT service explorer                 |

## MQTT Topic Structure

All topics are prefixed with `WALLBOX_TOPIC` (default: `easywallbox`):

### Status Topics (published by the bridge)
- `easywallbox/status/ble` - BLE connection state
- `easywallbox/status/general` - Online/offline status
- `easywallbox/message` - Raw BLE responses
- `easywallbox/ble/{key}/{index}` - Parsed BLE data

### Command Topics (subscribed by the bridge)
- `easywallbox/dpm` - DPM control (on/off/limit/status)
- `easywallbox/charge` - Charge control (start/stop with optional delay)
- `easywallbox/limit` - Limit control (dpm/safe/user)
- `easywallbox/read` - Read commands (manufacturing/settings/app_data/hw_settings/voltage)

### EVCC Integration
- `easywallbox/evcc/status` - EVCC status (A/B/C/D)
- `easywallbox/evcc/enabled` - EVCC enabled state
- `easywallbox/evcc/maxcurrent` - Set max current (amps)
- `easywallbox/evcc/maxcurrentmillis` - Set max current (milliamps)
- `easywallbox/evcc/enable` - Enable/disable EVCC control

## Error Handling and Exit Codes

A failed BLE operation (GATT write, connect, disconnect) is never fatal:
writes are retried a few times, then the command is dropped and the BLE
link is reset so the consumer loop reconnects and authenticates again.

| Exit code | Meaning                                                        |
|-----------|----------------------------------------------------------------|
| `0`       | Clean shutdown after `SIGTERM` or `SIGINT`                     |
| `1`       | Unrecoverable error: MQTT connection lost, or a task crashed   |
