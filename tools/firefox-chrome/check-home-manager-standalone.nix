{
  nixpkgsPath,
  homeManagerPath,
  homeDirectory,
  username,
}:
let
  pkgs = import nixpkgsPath { };
  system = pkgs.stdenv.hostPlatform.system;
  # The derivation the flake ships, not the source tree: their layouts differ
  # (clear-nova-pref.py sits at the package root).
  package = pkgs.callPackage ../../nix/pkgs/default.nix { };
  textfoxModule = import ../../nix/modules/home-manager.nix {
    self.packages.${system}.default = package;
  };
  evaluated = import (homeManagerPath + "/modules") {
    inherit pkgs;
    configuration = {
      imports = [ textfoxModule ];
      home.username = username;
      home.homeDirectory = homeDirectory;
      home.stateVersion = "26.05";
      textfox.enable = true;
      textfox.profiles = [ "test" ];
      textfox.config.border.width = "7px";
      textfox.config.font.size = "17px";
      textfox.config.displayTitles = false;
      textfox.config.tabs.vertical.enable = false;
      textfox.config.icons.context.firefox.enable = true;
    };
  };
in
evaluated.activationPackage
