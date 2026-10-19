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

      flake.nixosModules = rec {
        ew2mqtt = ./nix/nixos-module.nix;
        default = ew2mqtt;
      };

      perSystem =
        {
          inputs',
          pkgs,
          ...
        }:
        let
          ew2mqtt = pkgs.callPackage ./nix/package.nix { };

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

            treefmt = treefmt.check ./.;
          }
          // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
            nixos = import ./nix/nixos-test.nix {
              inherit pkgs;
              nixosModule = inputs.self.nixosModules.default;
            };
          };

          devShells.default = pkgs.mkShellNoCC {
            inherit (ew2mqtt) propagatedBuildInputs;

            packages = [ treefmt ];

            # shellHook = ''
            #   export PYTHONPATH=".:$PYTHONPATH"
            # '';
          };
        };
    };
}
