# Run the bridge as a systemd service next to BlueZ
{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.ew2mqtt;
in
{
  options.services.ew2mqtt = {
    enable = lib.mkEnableOption "the EasyWallbox to MQTT bridge";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "ew2mqtt.packages.\${pkgs.stdenv.hostPlatform.system}.default";
      description = "The ew2mqtt package to run.";
    };

    wallboxAddress = lib.mkOption {
      type = lib.types.str;
      example = "84:BA:20:56:8F:09";
      description = "BLE MAC address of the wallbox, or `demo` to run without one.";
    };

    mqttHost = lib.mkOption {
      type = lib.types.str;
      example = "10.10.107.2";
      description = "Host name or address of the MQTT broker.";
    };

    mqttPort = lib.mkOption {
      type = lib.types.port;
      default = 1883;
      description = "Port of the MQTT broker.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/agenix/easywallbox.env";
      description = ''
        File with more environment variables, such as `WALLBOX_PIN`,
        `MQTT_USERNAME` and `MQTT_PASSWORD`. systemd reads it as root, so it
        can be readable by root only.
      '';
    };

    bluezReset = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = ''
          Restart BlueZ, and the bridge with it, when the bridge keeps
          failing to reach the wallbox.
        '';
      };

      attempts = lib.mkOption {
        type = lib.types.ints.positive;
        default = 15;
        description = ''
          Failed reconnects before BlueZ gets restarted. The bridge backs off
          to 120s between attempts, so 15 is about 20 minutes down.
        '';
      };
    };
  };

  config = lib.mkIf cfg.enable {
    hardware.bluetooth.enable = lib.mkDefault true;

    environment.systemPackages = [ (lib.lowPrio cfg.package) ];

    # bluetoothd ships with Restart=no: a crash would leave the bridge
    # without BLE until something D-Bus activates BlueZ again.
    systemd.services.bluetooth.serviceConfig = {
      Restart = lib.mkDefault "on-failure";
      RestartSec = lib.mkDefault "5s";
    };

    systemd.services.ew2mqtt = {
      description = "EasyWallbox to MQTT bridge";

      # network.target does not wait for an address: on 2026-10-10 the
      # first MQTT connect failed with ENETUNREACH. BLE goes through
      # BlueZ on the system bus, so order after its D-Bus name is up.
      after = [
        "network-online.target"
        "bluetooth.service"
      ];
      wants = [
        "network-online.target"
        "bluetooth.service"
      ];

      # A BlueZ restart invalidates every D-Bus object the bridge holds, so
      # restart it together with BlueZ for a clean slate. PartOf= only
      # propagates stop and restart, and wantedBy brings it back when
      # BlueZ starts again.
      # BindsTo= is avoided: a failed BlueZ would stop the bridge for good,
      # with Restart= no longer applying.
      partOf = [ "bluetooth.service" ];
      wantedBy = [
        "multi-user.target"
        "bluetooth.service"
      ];

      # Never give up: an unattended bridge must keep retrying, the
      # growing RestartSec below is what keeps it from spinning.
      startLimitIntervalSec = 0;

      environment = {
        WALLBOX_ADDRESS = cfg.wallboxAddress;
        MQTT_HOST = cfg.mqttHost;
        MQTT_PORT = toString cfg.mqttPort;
      };

      serviceConfig = {
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
        ExecStart = lib.getExe' cfg.package "ew2mqtt";
        # READY=1 once MQTT is up (not waiting for the wallbox), then
        # WATCHDOG=1 every WatchdogSec/2 while it receives data or its
        # reconnect loop makes progress. A wedged loop stops the pings and
        # systemd kills it within about 2 minutes (90s grace plus WatchdogSec).
        Type = "notify";
        NotifyAccess = "main";
        WatchdogSec = "30s";
        KillSignal = "SIGINT";
        TimeoutStopSec = "30s";
        # The bridge keeps no state, the BLE bond lives in BlueZ, and BlueZ's
        # D-Bus policy lets any user talk to it.
        DynamicUser = true;

        # It exits 0 only when asked to stop and recovers BLE errors itself,
        # so on-failure would do; always keeps an unattended bridge up
        # whatever the reason it ended.
        Restart = "always";
        RestartSec = "5s";
        RestartSteps = 5;
        RestartMaxDelaySec = "1min";

        # Hardening limited to what cannot interfere with BLE: it only
        # needs the system D-Bus socket and outbound TCP to the broker.
        # Left out on purpose: SystemCallFilter (a denied syscall after a
        # Python or glibc bump kills the bridge), MemoryDenyWriteExecute
        # (libffi/ctypes trampolines need W+X), PrivateUsers (remaps the
        # credentials BlueZ and D-Bus policy see), IP address allow-lists
        # (would break the bridge if MQTT_HOST changes).
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        ProtectProc = "invisible";
        ProtectClock = true;
        ProtectHostname = true;
        ProtectKernelLogs = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        # AF_NETLINK lets glibc resolve a hostname should MQTT_HOST become one.
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
          "AF_NETLINK"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        SystemCallArchitectures = "native";
        RemoveIPC = true;
        UMask = "0077";
      };
    };

    # The bridge recovers its own BLE link, but not a stale BlueZ: on
    # 2026-10-14 a month-old bluetoothd timed out every connect while the
    # wallbox advertised at -72 dBm, and a bluetooth.service restart had
    # it reading data within 5s. The bridge keeps pinging the watchdog
    # while it retries, so restart BlueZ once its status reports enough
    # failed attempts; PartOf restarts the bridge with it.
    # The pattern matches the status EasyWallbox.progress() reports.
    systemd.services.ew2mqtt-bluez-reset = lib.mkIf cfg.bluezReset.enable {
      description = "Restart BlueZ when ew2mqtt cannot reach the wallbox";
      serviceConfig.Type = "oneshot";
      script = ''
        status=$(systemctl show -P StatusText ew2mqtt.service)
        if [[ $status =~ reconnecting\ after\ ([0-9]+)\ failed ]] &&
          ((BASH_REMATCH[1] >= ${toString cfg.bluezReset.attempts})); then
          echo "ew2mqtt: $status, restarting bluetooth.service"
          systemctl restart bluetooth.service
        fi
      '';
    };

    systemd.timers.ew2mqtt-bluez-reset = lib.mkIf cfg.bluezReset.enable {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "5min";
        OnUnitActiveSec = "2min";
      };
    };
  };
}
