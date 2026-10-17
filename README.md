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

### NixOS

`nixosModules.default` runs the bridge as a hardened systemd service, see
[`nix/nixos-module.nix`](nix/nixos-module.nix) for every option:

```nix
{
  imports = [ inputs.ew2mqtt.nixosModules.default ];

  services.ew2mqtt = {
    enable = true;
    wallboxAddress = "00:00:00:00:00:00";
    mqttHost = "mqtt.example.lan";
    # WALLBOX_PIN, MQTT_USERNAME and MQTT_PASSWORD
    environmentFile = "/run/secrets/ew2mqtt.env";
  };
}
```

It enables BlueZ, runs the bridge as a dynamic user restarted together with
`bluetooth.service`, and restarts BlueZ after 12 failed reconnects
(`bluezReset.attempts`), as a stale bluetoothd can time out every connect.
`standalone.enable`, `standalone.dpmLimit` and `standalone.userLimit` set
[standalone mode](#standalone) at startup.

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

| Variable                | Default        | Description                                                 |
|-------------------------|----------------|-------------------------------------------------------------|
| `WALLBOX_ADDRESS`       | `demo`         | Wallbox BLE MAC address; `demo` runs without a wallbox      |
| `WALLBOX_PIN`           | `9844`         | BLE PIN, see [Finding the PIN](#finding-the-pin)            |
| `WALLBOX_PASSKEY`       | `001234`       | BLE pairing passkey, see [Pairing](#pairing)                |
| `WALLBOX_TOPIC`         | `easywallbox`  | Prefix of every MQTT topic                                  |
| `MQTT_HOST`             | `192.168.2.70` | MQTT broker host                                            |
| `MQTT_PORT`             | `1883`         | MQTT broker port                                            |
| `MQTT_USERNAME`         | (empty)        | MQTT username                                               |
| `MQTT_PASSWORD`         | (empty)        | MQTT password                                               |
| `STANDALONE`            | `false`        | Start in [standalone mode](#standalone): `true` or `false`  |
| `STANDALONE_DPM_LIMIT`  | (none)         | Standalone DPM limit, 6-63 A; needed with `STANDALONE=true` |
| `STANDALONE_USER_LIMIT` | (none)         | Standalone user limit, 6-32 A; unset leaves it as it is     |

The MQTT client identifier is `easywallbox-mqtt`. In `demo` mode, BLE writes
are only logged and authentication succeeds after one second, which is useful
to test the MQTT side.

### Finding the address

Run `ble_scan`, or `bluetoothctl scan le`, and look for a device named like
`eWB01728`.

### Finding the PIN

The PIN is not the `001234` shown by the official app: that is the pairing
passkey, see [Pairing](#pairing). Scan the QR code on the sticker inside the
wallbox; it reads like:

```
BT:N:2911AA00101728;M:F2ME.EWE08APEFXX;D:eWB01728;P:001234;A:9844;;
```

| Field | Meaning     | Example            |
|-------|-------------|--------------------|
| `N`   | Serial      | `2911AA00101728`   |
| `M`   | Part number | `F2ME.EWE08APEFXX` |
| `D`   | BT name     | `eWB01728`         |
| `P`   | Passkey     | `001234`           |
| `A`   | BLE PIN     | `9844`             |

`WALLBOX_PIN` is the `A` value.

### Pairing

The wallbox pairs over BLE with passkey entry, using the `P` value: `001234` on
every unit seen so far, the default of `WALLBOX_PASSKEY`. On Linux the bridge
registers a BlueZ pairing agent that answers the passkey request for the
wallbox only, and pairs with bonding on its first connection. When the wallbox
forgets the bond, BlueZ reports an authentication failure: the bridge removes
the bond and pairs again on the next connection.

To pair by hand instead, stop the bridge, since the wallbox accepts one
connection at a time, and run `bluetoothctl`:

```
pairable on
agent KeyboardOnly
default-agent
scan le
pair 00:00:00:00:00:00
```

Enter the passkey when asked, check that `info 00:00:00:00:00:00` shows
`Bonded: yes`, then `pairable off`. A stale bond goes first with
`remove 00:00:00:00:00:00`. An adapter that is not pairable pairs without
bonding, so the keys are lost with the link.

## systemd

ew2mqtt implements the `sd_notify` protocol with the Python standard library;
without `NOTIFY_SOCKET` it runs as a plain process.

- `READY=1` once connected to MQTT, without waiting for the wallbox, which may
  be unreachable for hours
- `WATCHDOG=1` every `WatchdogSec / 2`, only while data arrived from the
  wallbox or the reconnect loop ran within the last 90 seconds (or 4 poll
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

| Topic                        | Payload                                                                                  |
|------------------------------|------------------------------------------------------------------------------------------|
| `easywallbox/status/general` | `online`, or `offline` on shutdown and as the MQTT last will                             |
| `easywallbox/status/ble`     | `disconnected`, `connecting`, `connected`, `paired`, `authenticated`, `terminating`      |
| `easywallbox/message`        | Every raw response line from the wallbox                                                 |
| `easywallbox/ble/<key>/<n>`  | Field `n` of a `<key>` response, for example `easywallbox/ble/AD/4`                      |
| `easywallbox/ble/<key>/<id>` | For `AL`, `SL` and `IDX` responses: the remaining fields, comma separated                |
| `easywallbox/evcc/status`    | evcc charging state `A`, `B`, `C` or `D`, see below                                      |
| `easywallbox/evcc/enabled`   | Whether evcc controls the charge: `true` or `false`, and `false` at startup and shutdown |

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
arrive while the wallbox is disconnected are dropped. The evcc commands below
are the exception.

### evcc

| Topic                               | Payload       | Action                                                      |
|-------------------------------------|---------------|-------------------------------------------------------------|
| `easywallbox/evcc/maxcurrent`       | Integer, A    | Set the user limit                                          |
| `easywallbox/evcc/maxcurrentmillis` | Decimal, A    | Set the user limit with 0.1 A resolution, for example `6.5` |
| `easywallbox/evcc/enable`           | `true`        | Turn DPM off, so evcc controls the current                  |
| `easywallbox/evcc/enable`           | anything else | Turn DPM on with a 1 A DPM limit, which holds the charge    |

The bridge holds the settings from the last evcc command: it writes them again
after every BLE login, so a command that arrived while the wallbox was
unreachable, or a write lost with the link, still reaches it. The wallbox
echoes every write, and its app data carries the DPM switch (field 6) and the
user limit (field 10): the bridge writes again a setting either shows with
another value. Only the echo confirms the DPM limit.

`easywallbox/evcc/status` is derived from field 4 of the app data,
`easywallbox/ble/AD/4`:

| `AD/4`   | evcc status | Meaning         |
|----------|-------------|-----------------|
| `3`      | `A`         | Available       |
| `5`, `6` | `B`         | Vehicle present |
| `8`      | `C`         | Charging        |
| other    | `D`         | Error           |

### Standalone

In standalone mode the bridge ignores evcc and leaves the charge current to the
wallbox's DPM: its power meter keeps the household under the DPM limit. Use it
when evcc cannot be trusted to reach the wallbox. On every BLE login the bridge
writes DPM on, the DPM limit and, when set, the user limit, and writes again a
setting the wallbox reports with another value, as for evcc. It also reads the
safe limit for monitoring: the reply goes to `easywallbox/message`. The wallbox
stores these settings, so it keeps protecting the supply while the link is down.

For example, a 6 kW supply at 230 V, capped at 5 kW to be safe: 5000 W / 230 V
is 21.7 A, so a DPM limit of `21`.

`STANDALONE`, `STANDALONE_DPM_LIMIT` and `STANDALONE_USER_LIMIT` set the
state at startup; this topic changes it at runtime, until the next restart:

| Topic                    | Payload    | Action                                              |
|--------------------------|------------|-----------------------------------------------------|
| `easywallbox/standalone` | `on`       | Turn standalone mode on; needs a DPM limit          |
| `easywallbox/standalone` | `off`      | Turn standalone mode off, evcc controls the wallbox |
| `easywallbox/standalone` | `dpm/<A>`  | Set the DPM limit, 6-63 A                           |
| `easywallbox/standalone` | `user/<A>` | Set the user limit, 6-32 A                          |
| `easywallbox/standalone` | `user/off` | Leave the user limit as it is                       |

Turning it on, or changing a limit while it is on, writes the settings at once
when logged in, as after a login. Turning it off leaves the
wallbox as it is until evcc's next command.

While it is on, the `easywallbox/evcc/enable`, `easywallbox/evcc/maxcurrent`
and `easywallbox/evcc/maxcurrentmillis` commands are ignored; the status
topics are still published. The `easywallbox/dpm` and `easywallbox/limit`
commands still work, but the next login writes the standalone settings again.

The bridge publishes its state, retained, at startup and on every change:

| Topic                               | Payload                                  |
|-------------------------------------|------------------------------------------|
| `easywallbox/standalone/enabled`    | `true` or `false`                        |
| `easywallbox/standalone/dpm_limit`  | DPM limit in A, or cleared when not set  |
| `easywallbox/standalone/user_limit` | User limit in A, or cleared when not set |

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
