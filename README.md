# ew2mqtt

A BLE to MQTT bridge for Free2Move EasyWallbox chargers. It keeps a Bluetooth
Low Energy session with the wallbox, publishes its state to an MQTT broker,
and turns MQTT messages into wallbox commands, so that home automation or
[evcc](https://evcc.io/) can drive the charger, for example to follow solar
production.

## Credits

ew2mqtt is a fork of [easywallbox](https://github.com/mihaistana/easywallbox)
by Mihai Stana, who wrote the original bridge and documented the wallbox BLE
commands and how to find the BLE PIN. The protocol reference at the end of
this page comes from that work.

Mihai built it to charge from surplus solar power: the wallbox DPM (Dynamic
Power Management) only balances the charge against the house consumption, so
he drove the charge limits over BLE instead of buying a new wallbox.

## Differences from upstream

- MQTT through [aiomqtt](https://github.com/empicano/aiomqtt) on asyncio,
  instead of the paho-mqtt callback API
- evcc integration: status mapping and current control topics
- Reconnects and logs in again after a BLE link loss, a silent link, or a
  login that is never answered, with exponential backoff and rate-limited
  logs during long outages
- Retries transient GATT errors instead of exiting, and exits non-zero on
  unrecoverable errors, so `Restart=on-failure` works
- systemd integration with `sd_notify`: `READY=1`, `WATCHDOG=1` only while the
  bridge makes progress, and `STATUS=` lines
- Runtime settings over MQTT under `<topic>/settings/`
- Sends the BLE logout command on shutdown
- `ble_scan` to find the wallbox address
- Packaged as a Nix flake

## Running

### Nix

```sh
nix build      # builds ./result/bin/ew2mqtt
nix run .      # builds and runs ew2mqtt
nix develop    # development shell
```

The flake exposes `packages.<system>.default`. As a flake input:

```nix
inputs.ew2mqtt.url = "github:nolith/ew2mqtt";
```

### Without Nix

ew2mqtt needs Python 3.10 or newer, [bleak](https://github.com/hbldh/bleak)
and aiomqtt. On Linux, bleak talks to BlueZ over D-Bus.

```sh
pip install .
```

### Commands

| Command                   | Description                                   |
|---------------------------|-----------------------------------------------|
| `ew2mqtt`                 | The MQTT bridge daemon                        |
| `ew2mqtt-terminal [ADDR]` | Interactive BLE terminal to send raw commands |
| `ble_scan`                | List nearby BLE devices with their addresses  |

In `ew2mqtt-terminal`, log in first by typing `$BLE,AUTH,<pin>`.

## Configuration

ew2mqtt reads its configuration from environment variables.

| Variable          | Default        | Description                                            |
|-------------------|----------------|--------------------------------------------------------|
| `WALLBOX_ADDRESS` | `demo`         | Wallbox BLE MAC address; `demo` runs without a wallbox |
| `WALLBOX_PIN`     | `9844`         | BLE PIN, see [Finding the PIN](#finding-the-pin)       |
| `WALLBOX_TOPIC`   | `easywallbox`  | Prefix of every MQTT topic                             |
| `MQTT_HOST`       | `192.168.2.70` | MQTT broker host                                       |
| `MQTT_PORT`       | `1883`         | MQTT broker port                                       |
| `MQTT_USERNAME`   | (empty)        | MQTT username                                          |
| `MQTT_PASSWORD`   | (empty)        | MQTT password                                          |

The MQTT client identifier is `easywallbox-mqtt`. In `demo` mode, BLE writes
are only logged and authentication succeeds after one second, which is useful
to test the MQTT side.

### Finding the address

Run `ble_scan`, or `bluetoothctl scan le`, and look for a device named like
`eWB01728`.

### Finding the PIN

The PIN is not the `001234` shown by the official app. Scan the QR code on the
sticker inside the wallbox; it reads like:

```
BT:N:2911AA00101728;M:F2ME.EWE08APEFXX;D:eWB01728;P:001234;A:9844;;
```

| Field | Meaning     | Example            |
|-------|-------------|--------------------|
| `N`   | Serial      | `2911AA00101728`   |
| `M`   | Part number | `F2ME.EWE08APEFXX` |
| `D`   | BT name     | `eWB01728`         |
| `P`   | Device PIN  | `001234`           |
| `A`   | BLE PIN     | `9844`             |

`WALLBOX_PIN` is the `A` value.

## systemd

ew2mqtt implements the `sd_notify` protocol with the Python standard library;
without `NOTIFY_SOCKET` it runs as a plain process.

- `READY=1` once connected to MQTT, without waiting for the wallbox, which may
  be unreachable for hours
- `WATCHDOG=1` every `WatchdogSec / 2`, only while data arrived from the
  wallbox or the reconnect loop ran within the last 60 seconds (or 4 poll
  intervals, if longer)
- `STATUS=` with the BLE state and the age of the last data
- `STOPPING=1` on shutdown

| Exit code | Meaning                                                                |
|-----------|------------------------------------------------------------------------|
| `0`       | Clean shutdown after `SIGTERM` or `SIGINT`                             |
| `1`       | Unrecoverable error: MQTT connection failed or lost, or a task crashed |

BLE errors never stop the process: it retries and reconnects on its own.

```ini
[Unit]
Description=EasyWallbox BLE to MQTT bridge
Wants=network-online.target
After=network-online.target bluetooth.target

[Service]
Type=notify
NotifyAccess=main
WatchdogSec=30s
Restart=on-failure
RestartSec=10s
ExecStart=/usr/local/bin/ew2mqtt
Environment=WALLBOX_ADDRESS=00:00:00:00:00:00
Environment=MQTT_HOST=mqtt.example.lan
# WALLBOX_PIN and MQTT_PASSWORD
EnvironmentFile=/etc/ew2mqtt/secrets.env

[Install]
WantedBy=multi-user.target
```

The process needs access to BlueZ on the system D-Bus to connect and pair.

## MQTT topics

In the tables below `easywallbox` is the default `WALLBOX_TOPIC`.

### Published by the bridge

| Topic                        | Payload                                                                                   |
|------------------------------|-------------------------------------------------------------------------------------------|
| `easywallbox/status/general` | `online`, or `offline` on shutdown and as the MQTT last will                              |
| `easywallbox/status/ble`     | `disconnected`, `connecting`, `connected`, `paired`, `authenticated`, `terminating`       |
| `easywallbox/message`        | Every raw response line from the wallbox                                                  |
| `easywallbox/ble/<key>/<n>`  | Field `n` of a `<key>` response, for example `easywallbox/ble/AD/4`                       |
| `easywallbox/ble/<key>/<id>` | For `AL`, `SL` and `IDX` responses: the remaining fields, comma separated                 |
| `easywallbox/evcc/status`    | evcc charging state `A`, `B`, `C` or `D`, see below                                       |
| `easywallbox/evcc/enabled`   | Whether evcc controls the charge: `false` at startup and shutdown, else `True` or `False` |

Responses are split on commas: in `$DATA,READ,AD,...` the key is `AD` and the
fields after it are numbered from 0. The last field keeps the trailing
newline. The bridge polls `$DATA,READ,AD` every `ble_refresh_interval`
seconds.

### Commands

These topics take a command as payload. A payload `raw/<command>` sends
`<command>` to the wallbox as is, so include the trailing newline. Limits are
in amperes.

| Topic                | Payload         | Action                       |
|----------------------|-----------------|------------------------------|
| `easywallbox/dpm`    | `on`            | Turn DPM on                  |
| `easywallbox/dpm`    | `off`           | Turn DPM off                 |
| `easywallbox/dpm`    | `limit`         | Read the DPM limit           |
| `easywallbox/dpm`    | `limit/<A>`     | Set the DPM limit            |
| `easywallbox/dpm`    | `status`        | Read the DPM status          |
| `easywallbox/charge` | `start`         | Start charging now           |
| `easywallbox/charge` | `start/<delay>` | Start charging after a delay |
| `easywallbox/charge` | `stop`          | Stop charging now            |
| `easywallbox/charge` | `stop/<delay>`  | Stop charging after a delay  |
| `easywallbox/limit`  | `dpm`           | Read the DPM limit           |
| `easywallbox/limit`  | `dpm/<A>`       | Set the DPM limit            |
| `easywallbox/limit`  | `safe`          | Read the safe limit          |
| `easywallbox/limit`  | `safe/<A>`      | Set the safe limit           |
| `easywallbox/limit`  | `user`          | Read the user limit          |
| `easywallbox/limit`  | `user/<A>`      | Set the user limit           |
| `easywallbox/read`   | `manufacturing` | Read manufacturing values    |
| `easywallbox/read`   | `settings`      | Read settings                |
| `easywallbox/read`   | `app_data`      | Read app data                |
| `easywallbox/read`   | `hw_settings`   | Read hardware settings       |
| `easywallbox/read`   | `voltage`       | Read the supply voltage      |

Commands are sent only while the BLE session is authenticated; commands that
arrive while the wallbox is disconnected are dropped.

### evcc

| Topic                               | Payload       | Action                                                      |
|-------------------------------------|---------------|-------------------------------------------------------------|
| `easywallbox/evcc/maxcurrent`       | Integer, A    | Set the user limit                                          |
| `easywallbox/evcc/maxcurrentmillis` | Decimal, A    | Set the user limit with 0.1 A resolution, for example `6.5` |
| `easywallbox/evcc/enable`           | `true`        | Turn DPM off, so evcc controls the current                  |
| `easywallbox/evcc/enable`           | anything else | Turn DPM on with a 1 A DPM limit, which holds the charge    |

`easywallbox/evcc/status` is derived from field 4 of the app data,
`easywallbox/ble/AD/4`:

| `AD/4`   | evcc status | Meaning         |
|----------|-------------|-----------------|
| `3`      | `A`         | Available       |
| `5`, `6` | `B`         | Vehicle present |
| `8`      | `C`         | Charging        |
| other    | `D`         | Error           |

### Settings

Publish a positive integer to `easywallbox/settings/<key>`. Unknown keys and
invalid values are logged and ignored.

| Key                    | Default | Description                                                                |
|------------------------|---------|----------------------------------------------------------------------------|
| `ble_refresh_interval` | `10`    | Seconds between app data polls; applies from the next BLE login            |
| `ble_retry_interval`   | `10`    | Seconds to wait after a failed reconnect; doubles per failure, up to 120 s |

## BLE protocol reference

Commands are ASCII lines terminated by `\n`, written to the RX
characteristic; answers arrive as notifications on TX. As documented
upstream:

| Description            | Command                       |
|------------------------|-------------------------------|
| Login                  | `$BLE,AUTH,<pin>\n`           |
| Logout                 | `$BLE,LOGOUT\n`               |
| Start charge           | `$CMD,CHARGE,START,<delay>\n` |
| Stop charge            | `$CMD,CHARGE,STOP,<delay>\n`  |
| Index write            | `$EEP,WRITE,IDX,<index>\n`    |
| Index read             | `$EEP,READ,IDX,<index>\n`     |
| Read alarms            | `$EEP,READ,AL,<alarm>\n`      |
| Read manufacturing     | `$EEP,READ,MF\n`              |
| Read charging sessions | `$EEP,READ,SL,<session>\n`    |
| Read settings          | `$EEP,READ,ST\n`              |
| Read app data          | `$DATA,READ,AD\n`             |
| Read HW settings       | `$DATA,READ,HS\n`             |
| Read supply voltage    | `$DATA,READ,SV\n`             |
| Set user limit         | `$EEP,WRITE,IDX,174,<dA>\n`   |
| Set DPM limit          | `$EEP,WRITE,IDX,158,<dA>\n`   |
| Set safe limit         | `$EEP,WRITE,IDX,156,<dA>\n`   |
| Set DPM off            | `$EEP,WRITE,IDX,178,0\n`      |
| Set DPM on             | `$EEP,WRITE,IDX,178,1\n`      |
| Get user limit         | `$EEP,READ,IDX,174\n`         |
| Get DPM limit          | `$EEP,READ,IDX,158\n`         |
| Get safe limit         | `$EEP,READ,IDX,156\n`         |
| Get DPM status         | `$EEP,READ,IDX,178\n`         |

Limits are in tenths of an ampere (`dA`): `160` is 16 A.

## Development

See [AGENTS.md](AGENTS.md) and the [docs](docs/) directory for the
architecture, code style and environment.
