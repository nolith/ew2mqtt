# The bridge, for callPackage: the flake and the NixOS module build it alike
{
  lib,
  stdenv,
  python3Packages,
}:
python3Packages.buildPythonApplication {
  pname = "ew2mqtt";
  version = "0.1.0";
  src = ../.;
  pyproject = true;

  build-system = [
    python3Packages.setuptools
  ];

  propagatedBuildInputs =
    with python3Packages;
    [
      aiomqtt
      bleak
    ]
    # bluez_pairing.py, Linux only, calls BlueZ over D-Bus itself
    ++ lib.optionals stdenv.hostPlatform.isLinux [ dbus-fast ];

  doCheck = false;
}
