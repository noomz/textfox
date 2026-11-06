{ nixpkgsPath, homeManagerPath }:
let
  pkgs = import nixpkgsPath { system = "x86_64-linux"; };
  package = pkgs.callPackage ../../nix/pkgs/default.nix { };
  textfox = import ../../nix/modules/home-manager.nix {
    self.packages.x86_64-linux.default = package;
  };
  system = import (nixpkgsPath + "/nixos/lib/eval-config.nix") {
    system = "x86_64-linux";
    modules = [
      (nixpkgsPath + "/nixos/modules/virtualisation/qemu-vm.nix")
      (homeManagerPath + "/nixos")
      (
        { pkgs, ... }:
        {
          system.stateVersion = "26.05";
          virtualisation.memorySize = 2048;
          documentation.nixos.enable = false;
          users.users.probe = {
            isNormalUser = true;
            createHome = true;
          };
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
          home-manager.users.probe = {
            imports = [ textfox ];
            home.stateVersion = "26.05";
            textfox.enable = true;
            textfox.profiles = [ "test" ];
            textfox.config.border.width = "7px";
          };
          systemd.services.firefox-probe = {
            wantedBy = [ "multi-user.target" ];
            after = [ "home-manager-probe.service" ];
            requires = [ "home-manager-probe.service" ];
            serviceConfig = {
              User = "probe";
              Environment = [
                "HOME=/home/probe"
                "MOZ_HEADLESS=1"
                "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1"
              ];
            };
            script = ''
              exec ${pkgs.firefox}/bin/firefox --marionette -remote-allow-system-access --new-instance --profile /home/probe/.config/mozilla/firefox/test about:blank
            '';
          };
          systemd.services.preferences-probe = {
            wantedBy = [ "multi-user.target" ];
            after = [ "firefox-probe.service" ];
            path = [ pkgs.systemd ];
            serviceConfig.Type = "oneshot";
            script = ''
              if ${pkgs.python3}/bin/python3 ${./probe-vm-preferences.py}; then
                echo PASS > /tmp/shared/result
              else
                echo FAIL > /tmp/shared/result
                journalctl -u home-manager-probe -u firefox-probe --no-pager -o cat > /tmp/shared/services.log
              fi
              systemctl poweroff
            '';
          };
        }
      )
    ];
  };
in
system.config.system.build.vm
