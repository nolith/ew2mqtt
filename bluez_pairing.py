"""Pairing with the wallbox through BlueZ, Linux only.

The wallbox pairs with passkey entry: it displays nothing, and expects the
6-digit P field from its sticker, 001234 on the units seen so far. bleak has
no pairing agent, so BlueZ needs one from us to answer RequestPasskey.

A wallbox that forgets its bond rejects the stored key: BlueZ then reports
the disconnect with org.bluez.Reason.Authentication, and the bond has to be
removed before pairing again.

With AlwaysPairable=false, BlueZ's default, registering a default agent
makes every adapter pairable, and so does an adapter appearing while the
agent is registered. The wallbox's adapter is kept at its value from before
the agent, pairable only while bonding.
"""

import contextlib
import logging

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.errors import DBusError
from dbus_fast.service import ServiceInterface, method

log = logging.getLogger()

BLUEZ = "org.bluez"
AGENT_PATH = "/ew2mqtt/agent"
REASON_AUTHENTICATION = "org.bluez.Reason.Authentication"


class PasskeyAgent(ServiceInterface):
    """A BlueZ agent that only pairs the wallbox."""

    def __init__(self, address, passkey):
        super().__init__("org.bluez.Agent1")
        self._device_suffix = "/dev_" + address.upper().replace(":", "_")
        self._passkey = passkey

    def _only_wallbox(self, device):
        # The agent is the default one for the whole adapter.
        if not device.endswith(self._device_suffix):
            log.warning("Rejecting a pairing request from %s", device)
            raise DBusError("org.bluez.Error.Rejected", "Not the wallbox")

    @method()
    def Release(self):
        pass

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        self._only_wallbox(device)
        log.info("Answering the wallbox's passkey request")
        return int(self._passkey)

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        self._only_wallbox(device)
        return self._passkey

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        pass

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        pass

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        self._only_wallbox(device)

    @method()
    def RequestAuthorization(self, device: "o"):
        self._only_wallbox(device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        self._only_wallbox(device)

    @method()
    def Cancel(self):
        log.warning("BlueZ cancelled the pairing")


class Pairing:
    """Registers the passkey agent and watches for rejected bonds."""

    def __init__(self, address, passkey):
        self._address = address
        self._device_suffix = "/dev_" + address.upper().replace(":", "_")
        self._agent = PasskeyAgent(address, passkey)
        self._bus = None
        self._auth_failed = False
        # Adapter path to its Pairable before the agent was registered
        self._pairable = {}

    async def start(self):
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._pairable = await self._adapters_pairable()
        self._bus.export(AGENT_PATH, self._agent)
        await self._call(
            "/org/bluez",
            "org.bluez.AgentManager1",
            "RegisterAgent",
            "os",
            [AGENT_PATH, "KeyboardOnly"],
        )
        await self._call(
            "/org/bluez",
            "org.bluez.AgentManager1",
            "RequestDefaultAgent",
            "o",
            [AGENT_PATH],
        )
        self._bus.add_message_handler(self._on_message)
        await self._call(
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "AddMatch",
            "s",
            [
                "type='signal',sender='org.bluez',"
                "interface='org.bluez.Device1',member='Disconnected'"
            ],
            destination="org.freedesktop.DBus",
        )
        log.info("BlueZ pairing agent registered")

    async def stop(self):
        if self._bus is None:
            return
        with contextlib.suppress(Exception):
            await self._call(
                "/org/bluez",
                "org.bluez.AgentManager1",
                "UnregisterAgent",
                "o",
                [AGENT_PATH],
            )
        self._bus.disconnect()
        self._bus = None

    def _on_message(self, msg):
        if (
            msg.message_type == MessageType.SIGNAL
            and msg.member == "Disconnected"
            and msg.path.endswith(self._device_suffix)
        ):
            reason = msg.body[0] if msg.body else ""
            log.info("BlueZ reports the wallbox disconnected: %s", reason)
            if reason == REASON_AUTHENTICATION:
                self._auth_failed = True
        return False

    def take_auth_failure(self):
        """Whether the wallbox rejected the bond since the last call."""
        failed, self._auth_failed = self._auth_failed, False
        return failed

    async def is_paired(self):
        path = await self._device_path()
        if path is None:
            return False
        reply = await self._call(
            path,
            "org.freedesktop.DBus.Properties",
            "Get",
            "ss",
            ["org.bluez.Device1", "Paired"],
        )
        return reply.body[0].value

    async def keep_pairable(self):
        """Undo BlueZ making the wallbox's adapter pairable on its own:
        another default agent, or the adapter plugged in again."""
        try:
            adapter = await self._adapter()
            if adapter is not None:
                await self._restore_pairable(adapter)
        except Exception as e:
            log.warning("Could not restore the adapter's Pairable: %s", e)

    @contextlib.asynccontextmanager
    async def bondable(self):
        """Make the wallbox's adapter pairable, so that BlueZ bonds.

        An adapter that is not pairable pairs without bonding: the keys
        are lost with the link.
        """
        adapter = await self._adapter()
        if adapter is not None and not await self._adapter_pairable(adapter):
            await self._set_adapter_pairable(adapter, True)
        try:
            yield
        finally:
            if adapter is not None:
                with contextlib.suppress(Exception):
                    await self._restore_pairable(adapter)

    async def _restore_pairable(self, adapter):
        # An adapter that was not there at start gets BlueZ's default
        wanted = self._pairable.setdefault(adapter, False)
        if await self._adapter_pairable(adapter) != wanted:
            await self._set_adapter_pairable(adapter, wanted)

    async def _adapter_pairable(self, adapter):
        reply = await self._call(
            adapter,
            "org.freedesktop.DBus.Properties",
            "Get",
            "ss",
            ["org.bluez.Adapter1", "Pairable"],
        )
        return reply.body[0].value

    async def _set_adapter_pairable(self, adapter, pairable):
        log.info("Setting %s pairable: %s", adapter, pairable)
        await self._call(
            adapter,
            "org.freedesktop.DBus.Properties",
            "Set",
            "ssv",
            ["org.bluez.Adapter1", "Pairable", Variant("b", pairable)],
        )

    async def _adapter(self):
        # The wallbox's own, None until BlueZ has seen the wallbox
        path = await self._device_path()
        return path.rsplit("/", 1)[0] if path else None

    async def _adapters_pairable(self):
        return {
            path: interfaces["org.bluez.Adapter1"]["Pairable"].value
            for path, interfaces in (await self._managed_objects()).items()
            if "org.bluez.Adapter1" in interfaces
        }

    async def _device_path(self):
        for path, interfaces in (await self._managed_objects()).items():
            device = interfaces.get("org.bluez.Device1")
            if device and device["Address"].value.upper() == self._address.upper():
                return path
        return None

    async def _managed_objects(self):
        reply = await self._call(
            "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", "", []
        )
        return reply.body[0]

    async def _call(self, path, interface, member, signature, body, destination=BLUEZ):
        reply = await self._bus.call(
            Message(
                destination=destination,
                path=path,
                interface=interface,
                member=member,
                signature=signature,
                body=body,
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise DBusError(reply.error_name, " ".join(map(str, reply.body)))
        return reply
