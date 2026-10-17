# Run the NixOS module against a local broker and a BlueZ without adapters
{
  pkgs,
  nixosModule,
}:
pkgs.testers.runNixOSTest {
  name = "ew2mqtt";

  # DPM keeps the household safe without evcc; demo mode logs every write
  nodes.standalone = {
    imports = [ nixosModule ];

    environment.systemPackages = [ pkgs.mosquitto ];

    services.mosquitto = {
      enable = true;
      listeners = [
        {
          address = "127.0.0.1";
          port = 1883;
          omitPasswordAuth = true;
          settings.allow_anonymous = true;
          acl = [ "topic readwrite #" ];
        }
      ];
    };

    services.ew2mqtt = {
      enable = true;
      wallboxAddress = "demo";
      mqttHost = "127.0.0.1";
      standalone = {
        enable = true;
        dpmLimit = 21;
        userLimit = 16;
      };
    };

    systemd.services.ew2mqtt.after = [ "mosquitto.service" ];
  };

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

    with subtest("bridge answers BlueZ's passkey request for the wallbox only"):
        machine.wait_until_succeeds(
            "journalctl -u ew2mqtt.service | grep 'BlueZ pairing agent registered'"
        )
        pid = machine.succeed("systemctl show -P MainPID ew2mqtt.service").strip()
        names = machine.succeed(
            f"busctl --no-legend list --unique | awk '$2 == {pid} {{print $1}}'"
        ).split()

        def request_passkey(device):
            # BlueZ runs as root, whose D-Bus policy may call agents
            return [
                machine.execute(
                    f"busctl call {name} /ew2mqtt/agent org.bluez.Agent1"
                    f" RequestPasskey o /org/bluez/hci0/{device} 2>&1"
                )
                for name in names
            ]

        replies = request_passkey("dev_00_11_22_33_44_55")
        assert (0, "u 1234\n") in replies, replies
        replies = request_passkey("dev_AA_BB_CC_DD_EE_FF")
        assert any("Not the wallbox" in out for _, out in replies), replies
        assert all(status != 0 for status, _ in replies), replies

    with subtest("bridge leaves Pairable alone without an adapter"):
        machine.wait_until_succeeds(
            "systemctl show -P StatusText ew2mqtt.service"
            " | grep 'reconnecting after [1-9][0-9]* failed'"
        )
        machine.fail("journalctl -u ew2mqtt.service | grep -i pairable")

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

    def wrote(index, value):
        # demo mode logs each write as bytearray(b'$EEP,WRITE,IDX,...\\n')
        standalone.wait_until_succeeds(
            "journalctl -u ew2mqtt.service | grep -F 'ble write'"
            f" | grep -F 'WRITE,IDX,{index},{value}\\n'"
        )

    def state(name, value):
        standalone.wait_until_succeeds(
            f"mosquitto_sub -h 127.0.0.1 -C 1 -W 5 -t easywallbox/standalone/{name}"
            f" | grep -x '{value}'"
        )

    def publish(topic, message):
        standalone.succeed(f"mosquitto_pub -h 127.0.0.1 -t easywallbox/{topic} -m '{message}'")

    with subtest("standalone mode holds DPM after login"):
        standalone.wait_for_unit("ew2mqtt.service")
        state("enabled", "true")
        state("dpm_limit", "21")
        state("user_limit", "16")
        wrote(178, 1)
        wrote(158, 210)
        wrote(174, 160)

    with subtest("standalone mode ignores evcc"):
        publish("evcc/enable", "true")
        standalone.wait_until_succeeds(
            "journalctl -u ew2mqtt.service"
            " | grep 'Standalone mode: ignoring evcc commands'"
        )
        standalone.fail("journalctl -u ew2mqtt.service | grep -F 'IDX,178,0'")

    with subtest("standalone mode switches at runtime"):
        publish("standalone", "dpm/25")
        state("dpm_limit", "25")
        wrote(158, 250)
        publish("standalone", "off")
        state("enabled", "false")
        publish("evcc/enable", "true")
        wrote(178, 0)
  '';
}
