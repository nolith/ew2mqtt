{
  description = "BLE to MQTT bridge for Free2Move EasyWallbox chargers";

  inputs = {
    flake-parts.url = "github:hercules-ci/flake-parts";
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      debug = true;
      imports = [ ];
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      perSystem =
        {
          inputs',
          pkgs,
          ...
        }:
        let
          pythonPackages = pkgs.python3Packages;
          propagatedBuildInputs = with pythonPackages; [
            aiomqtt
            bleak
          ];

          ew2mqtt = pythonPackages.buildPythonApplication {
            inherit propagatedBuildInputs;

            pname = "ew2mqtt";
            version = "0.1.0";
            src = ./.;
            pyproject = true;

            build-system = [
              pythonPackages.setuptools
            ];

            doCheck = false;
          };

          treefmt = pkgs.treefmt.withConfig {
            configFile = ./treefmt.toml;
            runtimeInputs = [
              pkgs.nixfmt
              pkgs.ruff
            ];
          };
        in
        {
          formatter = treefmt;

          packages.default = ew2mqtt;

          checks = {
            package = ew2mqtt;
          }
          // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
            nixos = import ./nix/nixos-test.nix { inherit pkgs ew2mqtt; };
          };

          devShells.default = pkgs.mkShellNoCC {
            inherit propagatedBuildInputs;

            packages = [ treefmt ];

            # shellHook = ''
            #   export PYTHONPATH=".:$PYTHONPATH"
            # '';
          };
        };
    };
}
