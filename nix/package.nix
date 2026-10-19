# The bridge, for callPackage: the flake and the NixOS module build it alike
{ python3Packages }:
python3Packages.buildPythonApplication {
  pname = "ew2mqtt";
  version = "0.1.0";
  src = ../.;
  pyproject = true;

  build-system = [
    python3Packages.setuptools
  ];

  propagatedBuildInputs = with python3Packages; [
    aiomqtt
    bleak
  ];

  doCheck = false;
}
