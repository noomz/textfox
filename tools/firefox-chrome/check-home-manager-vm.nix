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
            textfox.config.font.size = "17px";
            textfox.config.displayTitles = false;
            textfox.config.tabs.vertical.enable = false;
            textfox.config.icons.context.firefox.enable = true;
          };
          # Seed a used profile before Home Manager activates, so the run proves
          # migration rather than a fresh install. Home Manager owns user.js
          # wholesale (it writes the whole file from extraConfig and settings),
          # so the user's own preferences live in prefs.js, where Firefox keeps
          # them. The seed writes nothing Home Manager manages (profiles.ini,
          # user.js, chrome/userChrome.css, chrome/config.css, containers.json),
          # so activation meets no collision. It runs only once per disk.
          systemd.services.seed-profile = {
            wantedBy = [ "multi-user.target" ];
            before = [ "home-manager-probe.service" ];
            requiredBy = [ "home-manager-probe.service" ];
            serviceConfig = {
              Type = "oneshot";
              RemainAfterExit = true;
              User = "probe";
            };
            script = ''
              profile=/home/probe/.config/mozilla/firefox/test
              if [ ! -e "$profile/prefs.js" ]; then
                mkdir -p "$profile/chrome"
                printf '%s\n' \
                  'user_pref("browser.nova.enabled", true);' \
                  'user_pref("browser.shell.checkDefaultBrowser", false);' \
                  'user_pref("tf.test.sentinel", true);' > "$profile/prefs.js"
                printf 'kept by the user' > "$profile/chrome/user-notes.txt"
              fi
            '';
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
              if ${pkgs.python3}/bin/python3 ${./probe-vm-preferences.py} \
                --profile /home/probe/.config/mozilla/firefox/test --border 7px \
                --expect-vars '{"--tf-font-size":"17px","--tf-border-width":"7px","--tf-display-vertical-tabs":"none","--tf-display-titles":"none"}' \
                --expect-prefs '{"shyfox.enable.context.menu.icons":true,"shyfox.enable.ext.mono.toolbar.icons":false}' \
                --preserve-pref 'tf.test.sentinel=true' \
                --preserve-pref 'browser.shell.checkDefaultBrowser=false' \
                --preserve-file '/home/probe/.config/mozilla/firefox/test/chrome/user-notes.txt=kept by the user'; then
                # Firefox rewrites prefs.js from its live user values on shutdown;
                # the sentinel must survive that and Nova must persist as off.
                systemctl stop firefox-probe
                prefs=/home/probe/.config/mozilla/firefox/test/prefs.js
                cp "$prefs" /tmp/shared/persisted-prefs.js
                if grep -qxF 'user_pref("tf.test.sentinel", true);' "$prefs" &&
                   grep -qxF 'user_pref("browser.nova.enabled", false);' "$prefs"; then
                  echo PASS > /tmp/shared/result
                else
                  echo FAIL > /tmp/shared/result
                  echo 'prefs.js lost the sentinel or did not persist Nova off after shutdown' > /tmp/shared/probe-error.txt
                fi
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
