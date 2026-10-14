# Run the NixOS module against a local broker and a BlueZ without adapters
{
  pkgs,
  nixosModule,
}:
pkgs.testers.runNixOSTest {
  name = "ew2mqtt";

  nodes.machine = {
    imports = [ nixosModule ];

    # Loading the Bluetooth stack is enough for bluetoothd to start, every
    # BLE connect then fails as it would with the wallbox out of reach.
    boot.kernelModules = [ "bluetooth" ];

    environment.systemPackages = [ pkgs.mosquitto ];

    services.mosquitto = {
      enable = true;
      listeners = [
        {
          address = "127.0.0.1";
          port = 1883;
          users.ew2mqtt = {
            password = "secret";
            acl = [ "readwrite #" ];
          };
        }
      ];
    };

    services.ew2mqtt = {
      enable = true;
      wallboxAddress = "00:11:22:33:44:55";
      mqttHost = "127.0.0.1";
      environmentFile = pkgs.writeText "ew2mqtt.env" ''
        MQTT_USERNAME=ew2mqtt
        MQTT_PASSWORD=secret
      '';
      bluezReset.attempts = 1;
    };

    systemd.services.ew2mqtt.after = [ "mosquitto.service" ];
  };

  testScript = ''
    mqtt = "-h 127.0.0.1 -u ew2mqtt -P secret"

    def invocation(unit):
        return machine.succeed(f"systemctl show -P InvocationID {unit}").strip()

    machine.wait_for_unit("mosquitto.service")

    with subtest("all entry points are installed"):
        machine.succeed("command -v ew2mqtt ew2mqtt-terminal ble_scan")

    with subtest("bridge reports READY once connected to MQTT"):
        machine.wait_for_unit("ew2mqtt.service")

    with subtest("bridge maps the wallbox status to evcc"):
        # status 8 on the BLE topic is a charging wallbox, evcc status "C"
        machine.wait_until_succeeds(
            f"mosquitto_rr {mqtt} -W 5"
            " -t easywallbox/ble/AD/4 -m 8"
            " -e easywallbox/evcc/status"
            " | grep -x C"
        )

    with subtest("bridge reaches BlueZ as a dynamic user"):
        machine.wait_for_unit("bluetooth.service")
        machine.succeed(
            "test $(stat -c %u /proc/$(systemctl show -P MainPID ew2mqtt.service))"
            " -ge 61184"
        )
        machine.wait_until_succeeds(
            "journalctl -u ew2mqtt.service | grep 'No Bluetooth adapters found'"
        )
        machine.fail("journalctl -u ew2mqtt.service | grep AccessDenied")

    with subtest("failed reconnects restart BlueZ and the bridge"):
        machine.wait_until_succeeds(
            "systemctl show -P StatusText ew2mqtt.service"
            " | grep 'reconnecting after [1-9][0-9]* failed'"
        )
        bluez = invocation("bluetooth.service")
        bridge = invocation("ew2mqtt.service")
        machine.systemctl("start ew2mqtt-bluez-reset.service")
        machine.wait_for_unit("bluetooth.service")
        machine.wait_for_unit("ew2mqtt.service")
        assert invocation("bluetooth.service") != bluez, "BlueZ was not restarted"
        assert invocation("ew2mqtt.service") != bridge, "bridge was not restarted"

    with subtest("bridge shuts down cleanly"):
        machine.systemctl("stop ew2mqtt.service")
        machine.fail("systemctl is-failed ew2mqtt.service")
  '';
}
