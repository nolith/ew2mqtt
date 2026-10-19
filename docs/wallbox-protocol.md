# Wallbox protocol, as observed

This page documents what one EasyWallbox actually sends and accepts over BLE,
reconstructed from the bridge's own logs. It complements the
[MQTT topics](../README.md#mqtt-topics) in the README, which describe the
bridge's side.

## Source and confidence

All evidence comes from one wallbox, of two kinds:

- **Field observation**: what was seen on the wallbox and in the bridge's
  output while it was in use, mostly while developing the bridge in 2025:
  LED colour, behaviour on unplug, values read during sessions, command dumps.
- **Data analysis**: statistics over the bridge journal from 2026-09-04 to
  2026-10-17: 4414 `$DATA,READ,AD` replies up to 2026-10-15, 51 EEPROM write
  echoes, 232 logins, and the first EEPROM read reply on 2026-10-17.

Every meaning below is marked:

- **confirmed**: checked against an independent value;
- **inferred**: consistent with all frames, but not cross-checked;
- **unknown**: only the observed range.

Firmware version and model variant are not known; other units may differ.

Currents on the wire are in tenths of an ampere (dA): `82` is 8.2 A.

## Session

| Bridge sends      | Wallbox answers  | Notes                                     |
|-------------------|------------------|-------------------------------------------|
| `$BLE,AUTH,<pin>` | `$BLE,AUTH,OK`   | 232 of 253 logins answered in this window |
| `$BLE,LOGOUT`     | `$BLE,LOGOUT,OK` | seen once                                 |

No reply other than `OK` was ever logged for a login: an unanswered login just
times out.

## EEPROM writes and reads

```
bridge:  $EEP,WRITE,IDX,<index>,<value>\n
wallbox: $EEP,WRITE,IDX,<index>,<value>

bridge:  $EEP,READ,IDX,<index>\n
wallbox: $EEP,READ,IDX,<index>,<value>
```

- The wallbox **echoes the write verbatim**, 1-4 s later. There is no `OK` or
  `FAIL` token; no failure reply was ever logged.
- **A missing echo does not mean the write failed.** On 2026-10-15 at 12:42:14
  the bridge wrote `IDX,174,62`, the link reset before the echo arrived, and
  the next AD frames already showed the new user limit (field 10 = `62`, field
  3 = `103`). Confirm a write with the AD frame (below), not only with the echo.
- Settings are stored in EEPROM: they survive a lost link and a power cycle,
  and the wallbox keeps applying them without the bridge.
- A read answers with the stored value in the same shape as a write echo:
  `$EEP,READ,IDX,156,260` on 2026-10-17.

| Index | Setting    | Value                   | Read back in AD | Confidence                    |
|-------|------------|-------------------------|-----------------|-------------------------------|
| `174` | User limit | dA, observed `60`-`220` | field 10        | confirmed                     |
| `178` | DPM switch | `0` off, `1` on         | field 6         | confirmed                     |
| `158` | DPM limit  | dA, observed `10`       | not visible     | confirmed write, no read-back |
| `156` | Safe limit | dA, observed `260`      | field 9         | confirmed read, never written |

Over all 4414 frames, field 10 equalled the last echoed `174` value except
for the 7 frames after the lost echo above, and field 6 equalled the last
echoed `178` value in all 3855 frames that followed one.

## App data: `$DATA,READ,AD`

The bridge polls `$DATA,READ,AD` every `ble_refresh_interval` seconds, 10 by
default. The reply has 22 comma-separated fields, numbered from 0 after `AD`,
as in `easywallbox/ble/AD/<n>`:

```
$DATA,READ,AD,302,119,118,1000,3,0,1,0,0,260,82,49,0,0,0,0,0,268,1,38,0,0
```

| Field | Meaning                               | Unit    | Observed       | Confidence | Evidence                                                                                                                                                                |
|-------|---------------------------------------|---------|----------------|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 0     | unknown                               |         | 137-335        | unknown    | possibly a temperature in 0.1 °C, not checked                                                                                                                           |
| 1     | Control pilot voltage, positive level | 0.1 V   | 57-119         | confirmed  | Data analysis: ~12 V idle, ~9 V vehicle present, ~6 V charging, the J1772 levels                                                                                        |
| 2     | Control pilot voltage, negative level | 0.1 V   | 118-119        | inferred   | Data analysis: constant, matches the -12 V half of the pilot signal                                                                                                     |
| 3     | Control pilot PWM duty cycle          | 0.1 %   | 0-1000         | confirmed  | Data analysis: `1000` (no PWM) when not charging; while charging, duty × 0.6 = user limit: 366 ↔ 22.0 A, 155 ↔ 9.3 A, 100 ↔ 6.0 A                                       |
| 4     | Charging state                        |         | 3, 5, 6, 8, 12 | confirmed  | see below; drives `easywallbox/evcc/status`                                                                                                                             |
| 5     | flag                                  |         | 0-1            | unknown    | Data analysis: `1` only in the first state-12 frame                                                                                                                     |
| 6     | DPM switch                            | 0/1     | 0-1            | confirmed  | Data analysis: follows `IDX 178`                                                                                                                                        |
| 7     | Charging current                      | dA      | 0-215          | confirmed  | Data analysis: field 8 / (field 7 / 10) = 230.7 V median                                                                                                                |
| 8     | Charging power                        | W       | 0-4947         | confirmed  | see field 7                                                                                                                                                             |
| 9     | Safe limit                            | dA      | 260 (constant) | confirmed  | Data analysis: equals the `IDX 156` read reply, `260`, on 2026-10-17                                                                                                    |
| 10    | User limit                            | dA      | 60-220         | confirmed  | Data analysis: follows `IDX 174`                                                                                                                                        |
| 11    | Household current from the DPM meter  | dA      | 40-328         | inferred   | Data analysis: while charging, field 11 - field 7 = 5.5 A median, the rest of the house                                                                                 |
| 12    | always 0                              |         | 0              | unknown    |                                                                                                                                                                         |
| 13    | always 0                              |         | 0              | unknown    |                                                                                                                                                                         |
| 14    | Current flowing (contactor closed)    | 0/1     | 0-1            | inferred   | Data analysis: `1` in all 1672 state-8 frames with field 7 > 0, `0` in every other frame, also with 0.7 A residual in state 12                                          |
| 15    | always 0                              |         | 0              | unknown    |                                                                                                                                                                         |
| 16    | always 0                              |         | 0              | unknown    |                                                                                                                                                                         |
| 17    | Charging session counter              |         | 251-269        | confirmed  | Data analysis: +1 exactly when a charge starts (251→252, 252→253, 267→268) while fields 19-21 reset to 0. Field observation: `25` in 2025                               |
| 18    | Error in the current session          | 0/1     | 0-1            | confirmed  | Field observation: matches the wallbox LED turning red. Data analysis: set with state 12 in both journal cases, cleared at the next charge                              |
| 19    | Session energy                        | 0.1 kWh | 0-191          | confirmed  | Data analysis: one step per 97-110 Wh of integrated field 8 power over four charges                                                                                     |
| 20    | Charging minutes                      | min     | 0-192          | confirmed  | Field observation: 182 min against 192 connected in the wallbox's session log for one session. Data analysis: grew by 92, 77, 45, 36 over charges of 92, 78, 45, 36 min |
| 21    | Connected minutes                     | min     | 0-192          | confirmed  | Field observation: 192 min against 182 charging in the wallbox's session log for one session. Data analysis: equal to field 20 in all journal frames                    |

Fields 20 and 21 should differ when the car stays plugged in after it stops
drawing current, which the journal never captured.

### Charging state (field 4)

| Value | evcc | Meaning                                                      | Pilot (field 1) | Frames |
|-------|------|--------------------------------------------------------------|-----------------|--------|
| `3`   | `A`  | No vehicle                                                   | 11.8-11.9 V     | 2725   |
| `5`   | `B`  | Vehicle connected, charge held by the wallbox: no PWM        | 8.5-8.7 V       | 10     |
| `6`   | `B`  | Vehicle connected, current offered (PWM on), car not drawing | 5.8-8.6 V       | 2      |
| `8`   | `C`  | Charging                                                     | 5.7-5.8 V       | 1673   |
| `12`  |      | Error, inferred; observed cause: unplugged while charging    | 11.9 V          | 4      |

- `5`: data analysis: all 10 frames have duty `1000` (field 3) with DPM on (field 6 = `1`),
  as when evcc holds the charge with DPM on and a 1 A limit, its "off".
- `6`: data analysis: both frames came right after evcc turned DPM off and offered 6 A
  (duty `100`); the car started drawing within one poll, so the state looks
  short-lived but is not a transition of its own.
- `12`: data analysis: both journal cases (2026-09-04 06:38, session 251;
  2026-10-15 13:44, session 268) go from charging at about 21 A straight to
  an 11.9 V pilot within one poll, with field 18 (error) going to `1` and
  field 5 to `1` for one frame. Field observation: matches the wallbox LED
  turning red when the car is unplugged while charging. Field observation:
  unplugging during a charge logs alarm code `27`, shown as "GND
  disconnected" in the Android app. Other causes are expected from the
  wallbox's self-diagnostics, such as alarm codes `26` and `30` below; none
  has been observed.

The pilot levels and the duty-cycle rule (current = duty × 0.6 between 10 %
and 85 %) are the standard EV charging signal, see
[SAE J1772 signaling](https://en.wikipedia.org/wiki/SAE_J1772#Signaling);
IEC 61851-1 uses the same scheme.

### Using AD as read-back

- DPM on or off: field 6.
- User limit: field 10, cross-check with field 3 while charging.
- DPM limit (`IDX 158`): not visible in AD. Rely on the echo, or read it with
  `$EEP,READ,IDX,158`.
- Safe limit (`IDX 156`): field 9.

## Not yet documented

Field observations, not yet checked by data analysis.

- `$EEP,READ,ST` returns 27 values, likely EEPROM indexes `128 + 2 × i`:
  position 14 is `156` (`260`, the safe limit), position 23 is `174` (the
  user limit), position 25 is `178` (the DPM switch).
- `$EEP,READ,MF` returns manufacturing data; it contains `2022,2,25`.
- `$DATA,READ,HS` and `$DATA,READ,SV` exist; their replies are not decoded.
- Alarm log: 32 records of (index, session, code). Codes seen, with the
  error names the Android app shows: `26` DPM sensor fault, `27` GND
  disconnected (an unplug during a charge, see state `12`), `30` vehicle not
  ready.
- Session log: 16 records of (index, session, error, energy in 0.1 kWh,
  charging minutes, connected minutes).

## BLE link behaviour

Measured on 2026-10-15 with the bridge stopped:

- The wallbox advertises **slowly**: every 6-9 s typically, gaps up to 18 s
  and occasionally 37 s. Right after a disconnect it advertises several times
  per second for a while.
- A connect therefore needs a budget of 20-30 s: with a 10 s bleak timeout,
  the pending LE connection is cancelled before the next advertisement
  (HCI `LE Create Connection Cancel`, then `Unknown Connection Identifier
  (0x02)`), which the bridge logs as `TimeoutError`.
- Disconnect reasons worth knowing (HCI codes, Bluetooth Core Specification
  Vol 1 Part F):
  - `0x3e` Connection Failed to be Established: the wallbox never answered the
    connect request; with a good downlink this can point at the adapter's
    transmit side (weak dongle, poor placement).
  - `0x08` Connection Timeout: the link was up and then lost.
- The wallbox accepts one central at a time; while a phone holds it, the
  bridge cannot connect.

### Troubleshooting

```sh
# What the bridge is doing now
systemctl show -P StatusText ew2mqtt.service

# Raw HCI traffic: advertisements, connects, disconnect reasons
sudo btmon -w /tmp/x.btsnoop      # stop with Ctrl-C
sudo btmon -T -r /tmp/x.btsnoop | LC_ALL=C grep -a -E \
  'Advertising Report|Create Connection|Connection Complete|Disconnect|Reason:'
```

`bluetoothctl scan` prints a device's RSSI only when it changes, so a quiet
`bluetoothctl` does not prove the wallbox is silent; count the
`LE Extended Advertising Report` events in btmon instead.
