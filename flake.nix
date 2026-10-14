{
  description = "BLE to MQTT bridge for Free2Move EasyWallbox chargers";

  inputs = {
    flake-parts.url = "github:hercules-ci/flake-parts";
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
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
        "x86_64-darwin"
      ];
      perSystem =
        {
          inputs',
          pkgs,
          ...
        }:
        let
          pythonPackages = pkgs.python312Packages;
          propagatedBuildInputs =
            with pythonPackages;
            [
              aiomqtt
            ]
            ++ lib.optional (pkgs.stdenv.isLinux) bleak
            ++ lib.optionals (pkgs.stdenv.isDarwin) [
              (pythonPackages.callPackage ./nix/pyobjc-framework-corebluetooth.nix { })
              (pythonPackages.callPackage ./nix/pyobjc-framework-libdispatch.nix { })
              (pythonPackages.buildPythonPackage rec {
                pname = "bleak";
                version = bleak.version; # "0.22.3";
                format = "wheel";

                src = fetchPypi {
                  inherit pname version format;
                  hash = "sha256-5gE3E5bjV9le48JW22W32mJMlO9vBR1H386T6oNhwi4=";
                  dist = "py3";
                  python = "py3";
                  platform = "any";
                };
              })
              pyobjc-core
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
        in
        {
          formatter = pkgs.alejandra;

          packages.default = ew2mqtt;

          devShells.default = pkgs.mkShellNoCC {
            inherit propagatedBuildInputs;

            # shellHook = ''
            #   export PYTHONPATH=".:$PYTHONPATH"
            # '';
          };
        };
    };
}
