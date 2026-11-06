{ nixpkgsPath }:
let
  pkgs = import nixpkgsPath { system = "x86_64-linux"; };
  wrap = pkgs.callPackage ../../nix/pkgs/wrapTextfox.nix { };
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
          users.users.probe = {
            isNormalUser = true;
            createHome = true;
          };
          systemd.services.firefox-probe = {
            wantedBy = [ "multi-user.target" ];
            serviceConfig = {
              User = "probe";
              Environment = [
                "HOME=/home/probe"
                "MOZ_HEADLESS=1"
                "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1"
              ];
              Restart = "always";
              RestartSec = 2;
            };
            script = ''
              mkdir -p /home/probe/profile
              exec /run/current-system/sw/bin/firefox --marionette -remote-allow-system-access --new-instance --profile /home/probe/profile about:blank
            '';
          };
          systemd.services.verify-firefox-probe = {
            path = [
              pkgs.coreutils
              pkgs.findutils
              pkgs.gnugrep
              pkgs.systemd
            ];
            wantedBy = [ "multi-user.target" ];
            after = [ "firefox-probe.service" ];
            serviceConfig = {
              Type = "oneshot";
            };
            script = ''
              for i in $(seq 1 90); do
                if test -s /home/probe/profile/chrome/userChrome.css &&
                   test -s /home/probe/profile/chrome/userContent.css &&
                   find /home/probe/profile/chrome -maxdepth 1 -name '*-textfox-chrome' -print -quit | grep -q . &&
                   grep -q '0100007F:0B0C .* 0A ' /proc/net/tcp; then
                  echo PASS > /tmp/shared/result
                  systemctl poweroff
                  exit 0
                fi
                sleep 1
              done
              echo FAIL > /tmp/shared/result
              { journalctl -u firefox-probe.service --no-pager -o cat; ls -la /home/probe/profile/chrome; cat /proc/net/tcp; } > /tmp/shared/firefox.log 2>&1
              systemctl poweroff
              exit 1
            '';
          };
        }
      )
    ];
  };
in
system.config.system.build.vm
