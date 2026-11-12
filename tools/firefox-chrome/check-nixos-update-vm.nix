{ nixpkgsPath }:
let
  pkgs = import nixpkgsPath { system = "x86_64-linux"; };
  wrap = pkgs.callPackage ../../nix/pkgs/wrapTextfox.nix { };
  ffA = wrap pkgs.firefox-unwrapped { configCss = ":root { --tf-bg: #1111AA; }"; };
  ffB = wrap pkgs.firefox-unwrapped { configCss = ":root { --tf-bg: #2222BB; }"; };
  module = import ../../nix/modules/nixos.nix {
    self.packages.x86_64-linux.wrapTextfox = wrap;
  };
  system = import (nixpkgsPath + "/nixos/lib/eval-config.nix") {
    system = "x86_64-linux";
    modules = [
      (nixpkgsPath + "/nixos/modules/virtualisation/qemu-vm.nix")
      module
      (
        { pkgs, ... }:
        {
          system.stateVersion = "26.05";
          virtualisation.memorySize = 2048;
          documentation.nixos.enable = false;
          textfox.enable = true;
          systemd.services.textfox-update-scenario = {
            wantedBy = [ "multi-user.target" ];
            after = [
              "var.mount"
              "tmp-shared.mount"
            ];
            serviceConfig = {
              # Root, because the scenario has to shut the VM down and write its
              # verdict into the shared directory, neither of which a plain user
              # can do on its own.
              Type = "oneshot";
              Environment = [
                "HOME=/root"
                "MOZ_HEADLESS=1"
                "MOZ_FORCE_DISABLE_E10S=1"
                "MOZ_DISABLE_CONTENT_SANDBOX=1"
              ];
            };
            script = builtins.replaceStrings [ "FFGENA" "FFGENB" ] [ (toString ffA) (toString ffB) ] (
              builtins.readFile ./update-scenario-script.sh
            );
          };
        }
      )
    ];
  };
in
system.config.system.build.vm
