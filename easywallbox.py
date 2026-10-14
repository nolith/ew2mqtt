#!/usr/bin/env python3
import asyncio
import sys
from bleak import BleakClient
from bleak.exc import BleakError
import aiomqtt
import os
import commands
import mqttmap
import time
import signal
import logging
import settings
import sd_notify
from repeating_timer import RepeatingTimer
from blemap import WALLBOX_EPROM as eeprom
import blemap

FORMAT = ('%(asctime)-15s %(threadName)-15s '
          '%(levelname)-8s %(module)-15s:%(lineno)-8s %(message)s')
logging.basicConfig(format=FORMAT)
log = logging.getLogger()
log.setLevel(logging.INFO)

TOPIC = os.getenv('WALLBOX_TOPIC', 'easywallbox')
HOME_ASSISTANT_DISCOVERY = os.getenv('HOME_ASSISTANT_DISCOVERY', 'homeassistant')

SETTINGS_TOPIC_PREFIX = f"{TOPIC}/settings/"
COMMAND_TOPICS = {f"{TOPIC}/{name}": cmds for name, cmds in mqttmap.MQTT2BLE.items()}
EVCC_STATUS_SOURCE = f"{TOPIC}/ble/AD/4"
EVCC_COMMAND_MAXCURRENT = f"{TOPIC}/evcc/maxcurrent"
EVCC_COMMAND_MAXCURRENTMILLIS = f"{TOPIC}/evcc/maxcurrentmillis"
EVCC_COMMAND_ENABLE = f"{TOPIC}/evcc/enable"
EVCC_STATUS_AVAILABLE = "A"
EVCC_STATUS_VEHICLE_PRESENT = "B"
EVCC_STATUS_CHARGING = "C"
EVCC_STATUS_ERROR = "D"
EVCC_ENABLED = f"{TOPIC}/evcc/enabled"

settings = settings.Settings()

def mqtt_bool(value):
    # paho would publish a bool as "True"/"False"; evcc reads both, but the
    # bridge publishes and accepts lowercase
    return "true" if value else "false"

# Failures of a single BLE operation that leave the process healthy: they are
# retried or recovered by reconnecting, never fatal.
BLE_ERRORS = (BleakError, asyncio.TimeoutError, OSError, EOFError)
SHUTDOWN_TIMEOUT_SEC = 10

class EasyWallbox:
    WALLBOX_ADDRESS = os.getenv('WALLBOX_ADDRESS', 'demo')
    WALLBOX_PIN = os.getenv('WALLBOX_PIN', '9844')

    WALLBOX_RX = "a9da6040-0823-4995-94ec-9ce41ca28833";
    WALLBOX_SERVICE = "331a36f5-2459-45ea-9d95-6142f0c4b307";
    WALLBOX_ST = "75A9F022-AF03-4E41-B4BC-9DE90A47D50B";
    WALLBOX_TX = "a73e9a10-628f-4494-a099-12efaf72258f";
    WALLBOX_UUID ="0A8C44F5-F80D-8141-6618-2564F1881650";

    BLE_STATE_DISCONNECTED =    "disconnected";
    BLE_STATE_CONNECTING =       "connecting";
    BLE_STATE_CONNECTED =        "connected";
    BLE_STATE_CONNECTED_PAIRED =   "paired";
    BLE_STATE_CONNECTED_AUTH =   "authenticated";
    BLE_STATE_TERMINATING =      "terminating";

    WRITE_ATTEMPTS = 3
    WRITE_RETRY_DELAY_SEC = 1
    # bleak's own connect timeout, scan included; must leave time for
    # pairing and login within CONNECT_TIMEOUT_SEC
    BLEAK_CONNECT_TIMEOUT_SEC = 10
    CONNECT_TIMEOUT_SEC = 30
    AUTH_TIMEOUT_SEC = 20
    # Unanswered poll intervals before a silent link is considered dead
    STALE_POLL_INTERVALS = 3
    RECONNECT_BACKOFF_MAX_SEC = 120
    OUTAGE_LOG_INTERVAL_SEC = 600
    # Longest gap between progress marks (wallbox data or a reconnect loop
    # step) that still counts as healthy. Must exceed CONNECT_TIMEOUT_SEC.
    PROGRESS_TIMEOUT_SEC = 60
    LOGOUT_TIMEOUT_SEC = 3

    def __init__(self, queue, mqtt_client):
        self._client = BleakClient(self.WALLBOX_ADDRESS,
                                   disconnected_callback=self._on_disconnected,
                                   timeout=self.BLEAK_CONNECT_TIMEOUT_SEC)
        self._queue = queue
        self._lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self.state_timer = RepeatingTimer(self._update_ble_state)
        self._evcc_enabled = True
        self._mqtt_client = mqtt_client
        self._state = self.BLE_STATE_DISCONNECTED
        self._is_dpm_enabled = False
        self._rssi = 0
        self._resetting = False
        self._connecting = False
        self._disconnect_task = None
        self._poll_interval = None
        self._last_rx = 0.0
        self._last_progress = time.monotonic()
        self._auth_deadline = 0.0
        self._next_connect_at = 0.0
        self._failed_attempts = 0
        self._outage_since = time.monotonic()
        self._outage_logged_at = 0.0

    def is_demo(self):
        return self.WALLBOX_ADDRESS == "demo"

    async def state(self):
        async with self._state_lock:
            return self._state

    async def _set_state(self, state):
        async with self._state_lock:
            self._state = state
            sd_notify.notify(f"STATUS={self.progress()[1]}")
            await self._mqtt_client.publish(f"{TOPIC}/status/ble", state, qos=1)

    def _mark_rx(self):
        self._last_rx = self._last_progress = time.monotonic()

    def progress(self):
        """Return (making_progress, status) for the systemd watchdog.

        The bridge makes progress while it receives data from the wallbox,
        or while the reconnect loop is running. Being connected without any
        data does not count.
        """
        idle = time.monotonic() - self._last_progress
        timeout = max(self.PROGRESS_TIMEOUT_SEC,
                      (self.STALE_POLL_INTERVALS + 1) * (self._poll_interval or 0))
        if self._state == self.BLE_STATE_CONNECTED_AUTH:
            status = f"BLE authenticated, last data {idle:.0f}s ago"
        elif self._state == self.BLE_STATE_TERMINATING:
            return True, "Shutting down"
        else:
            status = f"BLE {self._state}, reconnecting after {self._failed_attempts} failed attempts"
        if idle > timeout:
            status = f"Stalled: no progress for {idle:.0f}s ({status})"
        return idle <= timeout, status

    async def is_connected(self):
        if self.is_demo():
            return await self.state() != self.BLE_STATE_DISCONNECTED
        return self._client.is_connected

    async def stop(self):
      state = await self.state()
      self.state_timer.stop()
      try:
        # before the logout, so that its answer is not an unexpected logout
        await self._set_state(self.BLE_STATE_TERMINATING)
      except aiomqtt.MqttError as e:
        log.warning("Could not publish BLE state: %s", e)
      async with self._lock:
        # The consumer loop is gone by now: write the logout directly.
        if state == self.BLE_STATE_CONNECTED_AUTH and await self.is_connected():
            try:
                await asyncio.wait_for(self.write(commands.logout()), self.LOGOUT_TIMEOUT_SEC)
            except BLE_ERRORS as e:
                log.warning("BLE logout failed: %s", e)
        if not self.is_demo():
            await self._client.disconnect()

    async def reset_connection(self):
        """Tear down the BLE link so that the consumer loop reconnects and
        authenticates again."""
        self.state_timer.stop()
        if await self.state() == self.BLE_STATE_TERMINATING:
            return
        if not self.is_demo():
            self._resetting = True
            try:
                await self._client.disconnect()
            except BLE_ERRORS as e:
                log.warning("BLE disconnect failed: %s", e)
            finally:
                self._resetting = False
        self._notification_buffer_rx = ""
        self._notification_buffer_st = ""
        if self._outage_since is None:
            self._outage_since = time.monotonic()
        self._next_connect_at = time.monotonic()
        await self._set_state(self.BLE_STATE_DISCONNECTED)

    def _on_disconnected(self, client):
        # bleak calls this from the event loop, for our own disconnect() too.
        # A drop during connect() surfaces there as an error or auth timeout.
        if self._resetting or self._connecting or self._state in (
                self.BLE_STATE_DISCONNECTED, self.BLE_STATE_TERMINATING):
            return
        log.warning("BLE link lost in state %s", self._state)
        self._disconnect_task = asyncio.create_task(self._handle_disconnect())

    async def _handle_disconnect(self):
        try:
            await self.reset_connection()
        except Exception:
            log.error("Failure handling BLE disconnect", exc_info=True)

    async def maintain_connection(self):
        """Run one step of the reconnect loop.

        Returns True when the link is authenticated and commands can be sent.
        """
        state = await self.state()
        now = time.monotonic()
        if state != self.BLE_STATE_CONNECTED_AUTH:
            self._last_progress = now  # the reconnect loop is alive
        if state == self.BLE_STATE_CONNECTED_AUTH:
            silent_for = now - self._last_rx
            if not await self.is_connected():
                log.warning("BLE link lost without a disconnect event")
            elif silent_for > self.STALE_POLL_INTERVALS * self._poll_interval:
                log.warning("No data from the wallbox for %ds, reconnecting", silent_for)
            else:
                return True
            await self.reset_connection()
        elif state in (self.BLE_STATE_CONNECTED, self.BLE_STATE_CONNECTED_PAIRED):
            if now > self._auth_deadline:
                await self._connect_failed("authentication timed out")
        elif state == self.BLE_STATE_DISCONNECTED:
            self._drop_queued_commands()
            if now >= self._next_connect_at:
                await self.connect()
        return False

    def _drop_queued_commands(self):
        dropped = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            dropped += 1
        if dropped:
            log.warning("Dropped %d BLE command(s): wallbox not connected", dropped)

    async def _connect_failed(self, error):
        now = time.monotonic()
        self._failed_attempts += 1
        retry_in_sec = await settings.get(settings.KEY_BLE_RETRY_INTERVAL)
        retry_in_sec = min(retry_in_sec * 2 ** min(self._failed_attempts - 1, 10),
                           self.RECONNECT_BACKOFF_MAX_SEC)
        # A long outage must not flood the journal: log the first failure,
        # then a summary every OUTAGE_LOG_INTERVAL_SEC.
        if self._failed_attempts == 1 or now - self._outage_logged_at >= self.OUTAGE_LOG_INTERVAL_SEC:
            log.error("BLE connection to %s failed (%d attempts, down for %ds): %s. "
                      "Retrying in %ds, next report in %ds.",
                      self.WALLBOX_ADDRESS, self._failed_attempts,
                      now - self._outage_since, str(error) or type(error).__name__,
                      retry_in_sec, self.OUTAGE_LOG_INTERVAL_SEC)
            self._outage_logged_at = now
        else:
            log.debug("BLE connection attempt %d failed: %s", self._failed_attempts, error)
        await self.reset_connection()
        self._next_connect_at = now + retry_in_sec

    async def write(self, data):
        if isinstance(data, str):
            data = bytearray(data, 'utf-8')
        if self.is_demo():
            # nothing will answer, count the write as wallbox activity
            self._mark_rx()
        else:
            await self._client.write_gatt_char(self.WALLBOX_RX, data)
        log.info("ble write: %s", data)

    async def send(self, data):
        """Write a command, retrying transient GATT failures.

        If the write keeps failing the command is dropped and the connection
        is reset, so that the consumer loop starts over with a fresh link.
        """
        for attempt in range(1, self.WRITE_ATTEMPTS + 1):
            try:
                await self.write(data)
                return True
            except BLE_ERRORS as e:
                log.warning("BLE write failed (attempt %d/%d): %s",
                            attempt, self.WRITE_ATTEMPTS, e)
            if attempt < self.WRITE_ATTEMPTS:
                await asyncio.sleep(self.WRITE_RETRY_DELAY_SEC)

        log.error("Dropping BLE command %r and resetting the connection", data)
        await self.reset_connection()
        return False

    async def _pair(self):
        state = await self.state()
        if state != self.BLE_STATE_CONNECTED:
            log.warn("_pair called in state %s", state)
            return

        await self._set_state(self.BLE_STATE_CONNECTED_PAIRED)
        log.info("Pairing BLE...")
        paired = sys.platform == "darwin" or self.is_demo() or await self._client.pair(protection_level=2)
        log.info(f"Paired: {paired}")
        await self._set_state(self.BLE_STATE_CONNECTED_PAIRED)

        if self.is_demo():
            return

        await self._client.start_notify(self.WALLBOX_TX, self._notification_handler_rx) #TX NOTIFY
        log.info("TX NOTIFY STARTED")
        await self._client.start_notify(self.WALLBOX_ST, self._notification_handler_st) #ST NOTIFY (CANAL BUSMODE)
        log.info("ST NOTIFY STARTED")

    async def _auth(self):
        state = await self.state()
        if state != self.BLE_STATE_CONNECTED_PAIRED:
            log.warn("_auth called in state %s", state)
            return

        log.info("BLE AUTH START: %s", self.WALLBOX_PIN)
        await self.write(commands.authBle(self.WALLBOX_PIN))
        if self.is_demo():
            await asyncio.sleep(1)
            await self._auth_completed()

    async def _auth_completed(self):
        state = await self.state()
        if state != self.BLE_STATE_CONNECTED_PAIRED:
            log.warn("_auth_completed called in state %s", state)
            return

        # set before the state change: maintain_connection() relies on them
        self._poll_interval = await settings.get(settings.KEY_BLE_REFRESH_INTERVAL)
        self._mark_rx()
        await self._set_state(self.BLE_STATE_CONNECTED_AUTH)
        if self._outage_since is None:
            log.info("BLE AUTH COMPLETE")
        else:
            log.info("BLE AUTH COMPLETE after %d failed attempts, down for %ds",
                     self._failed_attempts, time.monotonic() - self._outage_since)
        self._failed_attempts = 0
        self._outage_since = None
        # TODO pull the settings
        log.info("Starting ble state timer...")
        self.state_timer.start(self._poll_interval)

    async def connect(self):
        state = await self.state()
        if state != self.BLE_STATE_DISCONNECTED:
            log.warn("Connect called in state %s", state)
            return

        self._connecting = True
        try:
            await self._set_state(self.BLE_STATE_CONNECTING)
            log.debug("Connecting BLE...")
            await asyncio.wait_for(self._connect_and_auth(), self.CONNECT_TIMEOUT_SEC)
        except aiomqtt.MqttError:
            raise
        except Exception as e:
            await self._connect_failed(e)
        else:
            self._auth_deadline = time.monotonic() + self.AUTH_TIMEOUT_SEC
        finally:
            self._connecting = False

    async def _connect_and_auth(self):
        if not self.is_demo():
            await self._client.connect()
        log.info(f"Connected on {self.WALLBOX_ADDRESS}: {await self.is_connected()}")
        await self._set_state(self.BLE_STATE_CONNECTED)
        await self._pair()
        await self._auth()

    async def _update_ble_state(self):
        log.info("update ble state...")
        await self.read_application_data()
        log.info("ble state update done!")

    async def _enqueue_ble_command(self, cmd_key):
        log.info("_enqueue_ble_command %s", cmd_key)
        ble_cmd = eeprom[cmd_key]
        if ble_cmd is None:
            log.error(f"Unknown command key: {cmd_key}")
        else:
            log.info("Enqueue BLE cmd %s", ble_cmd)
        await self._queue.put(ble_cmd)

    async def read_application_data(self):
        await self._enqueue_ble_command("READ_APP_DATA")

    async def read_voltage(self):
        await self._enqueue_ble_command("READ_SUPPLY_VOLTAGE")

    async def evcc_enable(self, enable=True):
        log.warn("EVCC enable")
        if await self.state() != self.BLE_STATE_CONNECTED_AUTH:
            log.warn("Connection not authorized, ignoring evcc enable command")
            return

        if enable:
            await self._queue.put(commands.setDpmOff())
        else:
            await self._queue.put(commands.setDpmOn())
            # this is very tricky - I could not find a way to disable/pause a charge process
            # so the only way is set the limit to 1A so that the DPM will not allow the charge to start
            await self._queue.put(commands.setDpmLimit(1))

    def is_evcc_enabled(self):
        return not self._is_dpm_enabled

    _notification_buffer_rx = ""
    async def _notification_handler_rx(self, sender, data):
        self._mark_rx()
        self._notification_buffer_rx += data.decode()
        if "\n" in self._notification_buffer_rx:
            log.info("_notification RX received: %s", self._notification_buffer_rx)
            try:
                await self._mqtt_client.publish(topic=f"{TOPIC}/message", payload=self._notification_buffer_rx, qos=0, retain=False)

                match self._notification_buffer_rx:
                    case blemap.ANSWER_AUTHOK:
                        await self._auth_completed()
                    case blemap.ANSWER_LOGOUT:
                        state = await self.state()
                        if state != self.BLE_STATE_TERMINATING:
                            log.warn("Unexpected logout, authenticating again")
                            self.state_timer.stop()
                            await self._set_state(self.BLE_STATE_CONNECTED_PAIRED)
                            self._auth_deadline = time.monotonic() + self.AUTH_TIMEOUT_SEC
                            await self._auth()
                    case _:
                        chunks = self._notification_buffer_rx.split(",")
                        if len(chunks) > 3:
                            key = chunks[2]
                            data = chunks[3:]
                            if key == "AD" and len(data) > 6:
                                dpm_status = data[6]
                                log.info(f"DPM status: {dpm_status}")
                                self._is_dpm_enabled = (dpm_status != "0")
                            if key in ["AL", "SL", "IDX"]:
                                key += f"/{data[0]}"
                                data = ",".join(data[1:])
                                await self._mqtt_client.publish(topic=f"{TOPIC}/ble/{key}", payload=data, qos=1, retain=False)
                            else:
                                for idx, value in enumerate(data):
                                    await self._mqtt_client.publish(topic=f"{TOPIC}/ble/{key}/{idx}", payload=value, qos=1, retain=False)

            except Exception:
                # bleak runs this handler in a task nobody awaits
                log.error("Failure handling BLE notification", exc_info=True)
            finally:
                self._notification_buffer_rx = "";

    _notification_buffer_st = ""
    async def _notification_handler_st(self, sender, data):
        self._mark_rx()
        self._notification_buffer_st += data.decode()
        if "\n" in self._notification_buffer_st:
            log.info("_notification ST received: %s", self._notification_buffer_st)
            self._notification_buffer_st = "";


# Jun 12 20:05:53 ew2mqtt easywallbox-mqtt[98318]: 2025-06-12 20:05:53,411 MainThread      INFO     easywallbox    :172      _notification RX received: $BLE,LOGOUT$DATA,OK
async def main():
    mqtt_host = os.getenv('MQTT_HOST', '192.168.2.70')
    mqtt_port = int(os.getenv('MQTT_PORT', 1883))
    mqtt_username = os.getenv('MQTT_USERNAME', "")
    mqtt_password = os.getenv('MQTT_PASSWORD', "")

    queue = asyncio.Queue()

    # The callback for when a PUBLISH message is received from the server.
    async def on_message(client):
        await client.subscribe([
            # legacy
            *[(topic, 0) for topic in COMMAND_TOPICS]
            # settings
            ,(f"{SETTINGS_TOPIC_PREFIX}+", 0)
            # evcc - status
            ,(EVCC_STATUS_SOURCE, 0)
            # evcc - commands
            ,(EVCC_COMMAND_MAXCURRENT, 0), (EVCC_COMMAND_MAXCURRENTMILLIS, 0), (EVCC_COMMAND_ENABLE, 0)
            ])
        async for msg in client.messages:
            topic = str(msg.topic)
            message = msg.payload.decode()
            log.info(f"Message received [{topic}]: {message}")
            ble_command = None

            try:
                if topic == EVCC_STATUS_SOURCE:
                    status_code = int(message)
                    evcc_status = EVCC_STATUS_ERROR
                    if status_code == 3:
                        evcc_status =  EVCC_STATUS_AVAILABLE
                    elif status_code == 6 or status_code == 5:
                        evcc_status = EVCC_STATUS_VEHICLE_PRESENT
                    elif status_code == 8:
                        evcc_status = EVCC_STATUS_CHARGING
                    else:
                        log.warn(f"Unknown EVCC status: {status_code}")
                    await client.publish(f"{TOPIC}/evcc/status", evcc_status, qos=0, retain=False)
                    await client.publish(EVCC_ENABLED, mqtt_bool(eb.is_evcc_enabled()), qos=0, retain=False)
                    continue
                elif topic == EVCC_COMMAND_ENABLE:
                    await eb.evcc_enable(message == "true")
                    continue
                elif topic == EVCC_COMMAND_MAXCURRENT:
                    max_current = int(message)
                    ble_command = commands.setUserLimit(max_current, millis=False)
                elif topic == EVCC_COMMAND_MAXCURRENTMILLIS:
                    # evcc will send a float number, multiply by 10 to get mA
                    max_current = int(round(float(message)*10))
                    ble_command = commands.setUserLimit(max_current, millis=True)

                elif topic.startswith(SETTINGS_TOPIC_PREFIX):
                    opt = topic.removeprefix(SETTINGS_TOPIC_PREFIX)
                    log.info(f"Updating setting {opt}={message}")
                    try:
                        value = int(message)
                    except ValueError as e:
                        log.error(f"Invalid settings value for {opt}: {e}")
                        continue
                    if opt not in await settings.keys():
                        log.error(f"Unknown setting {opt}")
                    elif value <= 0:
                        log.error(f"Invalid settings value for {opt}: must be positive")
                    else:
                        await settings.set(opt, value)
                    continue

                elif("/" in message):
                    msx = message.split("/") #limit/10
                    if msx[0] == "raw":
                        log.info(f"Raw command: {msx[1]}")
                        ble_command = msx[1]
                    else:
                        #TODO: Check if msx contains valid values
                        ble_command = COMMAND_TOPICS[topic][msx[0]+"/"](msx[1])
                else:
                    ble_command = COMMAND_TOPICS[topic][message]
            except Exception:
                log.error(f"Failure processing MQTT message.", exc_info=True)
                pass

            if(ble_command != None):
                await queue.put(ble_command)

    async with aiomqtt.Client(
        hostname=mqtt_host, port=mqtt_port,
        username=mqtt_username, password=mqtt_password,
        identifier="easywallbox-mqtt",
        keepalive=60,
        will=aiomqtt.Will(f"{TOPIC}/status/general", "offline"),
    ) as client:
        log.info("[MQTT] Connected.")

        eb = EasyWallbox(queue, client)

        await client.publish(f"{TOPIC}/status/general", "online", qos=1)
        await client.publish(EVCC_ENABLED, mqtt_bool(False), qos=1)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        tasks = [
            asyncio.create_task(on_message(client), name="mqtt"),
            asyncio.create_task(queue_consumer(eb, queue), name="ble"),
        ]
        watchdog_timeout = sd_notify.watchdog_timeout()
        if watchdog_timeout:
            tasks.append(asyncio.create_task(watchdog(eb, watchdog_timeout), name="watchdog"))
        # Do not wait for the wallbox: it can be unreachable for hours.
        sd_notify.notify("READY=1", f"STATUS={eb.progress()[1]}")
        stop_task = asyncio.create_task(stop.wait(), name="signal")
        try:
            done, _ = await asyncio.wait(tasks + [stop_task],
                                         return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks + [stop_task]:
                task.cancel()
            await asyncio.gather(*tasks, stop_task, return_exceptions=True)
            await shutdown(client, eb)

        if stop_task in done:
            log.info("Stopped by signal")
            return

        # Every task runs forever: getting here means one of them failed.
        for task in done:
            if task.exception() is not None:
                raise task.exception()
        raise RuntimeError(f"{done.pop().get_name()} task exited unexpectedly")

async def watchdog(eb, timeout):
    log.info("systemd watchdog enabled, timeout %ss", timeout)
    healthy = True
    while True:
        progress, status = eb.progress()
        if progress:
            sd_notify.notify("WATCHDOG=1", f"STATUS={status}")
            if not healthy:
                log.warning("Progress resumed, watchdog pings restored")
        else:
            sd_notify.notify(f"STATUS={status}")
            if healthy:
                log.error("%s, withholding watchdog pings", status)
        healthy = progress
        await asyncio.sleep(timeout / 2)

async def shutdown(client, eb):
    log.info("Shutting down")
    sd_notify.notify("STOPPING=1")
    # Best effort: never let a cleanup failure replace the original error.
    try:
        await asyncio.wait_for(eb.stop(), SHUTDOWN_TIMEOUT_SEC)
    except Exception:
        log.warning("BLE shutdown failed", exc_info=True)
    try:
        await client.publish(f"{TOPIC}/status/general", "offline", qos=1)
        await client.publish(EVCC_ENABLED, mqtt_bool(False), qos=1)
    except aiomqtt.MqttError as e:
        log.warning("Could not publish offline status: %s", e)

async def queue_consumer(eb, queue):
    while True:
        if await eb.maintain_connection() and not queue.empty():
            item = queue.get_nowait()
            log.info(f"Consuming item {item}...")
            await eb.send(item)
        await asyncio.sleep(1)

def cli_main():
    try:
        asyncio.run(main())
    except Exception:
        log.critical("Fatal error, exiting", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    cli_main()
