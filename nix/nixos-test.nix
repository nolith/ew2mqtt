# Install ew2mqtt on NixOS and run it in demo mode against a local broker
{
  pkgs,
  ew2mqtt,
}:
pkgs.testers.runNixOSTest {
  name = "ew2mqtt";

  nodes.machine = { lib, ... }: {
    environment.systemPackages = [
      ew2mqtt
      pkgs.mosquitto
    ];

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

    # Mirrors the unit documented in docs/environment.md
    systemd.services.ew2mqtt = {
      wantedBy = [ "multi-user.target" ];
      requires = [ "mosquitto.service" ];
      after = [ "mosquitto.service" ];
      environment = {
        WALLBOX_ADDRESS = "demo";
        MQTT_HOST = "127.0.0.1";
      };
      serviceConfig = {
        Type = "notify";
        NotifyAccess = "main";
        DynamicUser = true;
        ExecStart = lib.getExe' ew2mqtt "ew2mqtt";
      };
    };
  };

  testScript = ''
    machine.wait_for_unit("mosquitto.service")

    with subtest("all entry points are installed"):
        machine.succeed("command -v ew2mqtt ew2mqtt-terminal ble_scan")

    with subtest("bridge reports READY once connected to MQTT"):
        machine.wait_for_unit("ew2mqtt.service")

    with subtest("bridge maps the wallbox status to evcc"):
        # status 8 on the BLE topic is a charging wallbox, evcc status "C"
        machine.wait_until_succeeds(
            "mosquitto_rr -h 127.0.0.1 -W 5"
            " -t easywallbox/ble/AD/4 -m 8"
            " -e easywallbox/evcc/status"
            " | grep -x C"
        )

    with subtest("bridge shuts down cleanly"):
        machine.systemctl("stop ew2mqtt.service")
        machine.fail("systemctl is-failed ew2mqtt.service")
  '';
}
