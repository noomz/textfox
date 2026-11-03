{ nixpkgsPath, homeManagerPath }:
let
  pkgs = import nixpkgsPath { };
  system = pkgs.stdenv.hostPlatform.system;
  package = ../..;
  textfoxModule = import ../../nix/modules/home-manager.nix {
    self.packages.${system}.default = package;
  };
  evaluated = import (homeManagerPath + "/modules") {
    inherit pkgs;
    configuration = {
      imports = [ textfoxModule ];
      home.username = "tester";
      home.homeDirectory = "/home/tester";
      home.stateVersion = "26.05";
      textfox.enable = true;
      textfox.profiles = [ "test" ];
      textfox.config.border.width = "7px";
      textfox.config.font.family = "monospace";
    };
  };
in
{
  config = evaluated.config;
  activationPackage = evaluated.activationPackage;
}
