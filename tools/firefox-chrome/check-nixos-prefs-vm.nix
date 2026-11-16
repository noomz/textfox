{ nixpkgsPath }:
# The NixOS module migrating a profile an earlier textfox already ran in: Nova
# persisted false (what textfox assigned before T25), a preference and a file
# textfox knows nothing about, and no user.js. The autoconfig must clear that
# false once, so Nova is back at Firefox's default, set textfox.nova.cleared,
# install the CSS and leave the rest alone, and a graceful shutdown must persist
# all of it to prefs.js.
let
  pkgs = import nixpkgsPath { system = "x86_64-linux"; };
  wrap = pkgs.callPackage ../../nix/pkgs/wrapTextfox.nix { };
  module = import ../../nix/modules/nixos.nix {
    self.packages.x86_64-linux.wrapTextfox = wrap;
  };
  profile = "/home/probe/profile";
  unrelated = "textfox must not touch this file";
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
          textfox.config.border.width = "11px";
          users.users.probe = {
            isNormalUser = true;
            createHome = true;
          };
          systemd.services.seed-probe-profile = {
            # The profile a user had before enabling textfox. Only seeded when
            # absent, so a reused disk image is never reset under the test.
            before = [ "firefox-probe.service" ];
            serviceConfig = {
              Type = "oneshot";
              User = "probe";
              RemainAfterExit = true;
            };
            script = ''
              if [ ! -e ${profile}/prefs.js ]; then
                mkdir -p ${profile}
                cat > ${profile}/prefs.js <<'EOF'
              user_pref("browser.nova.enabled", false);
              user_pref("browser.newtabpage.activity-stream.nova.enabled", true);
              user_pref("tf.test.sentinel", true);
              user_pref("browser.shell.checkDefaultBrowser", false);
              EOF
                printf '%s\n' '${unrelated}' > ${profile}/unrelated.txt
              fi
            '';
          };
          systemd.services.firefox-probe = {
            wantedBy = [ "multi-user.target" ];
            after = [ "seed-probe-profile.service" ];
            requires = [ "seed-probe-profile.service" ];
            serviceConfig = {
              User = "probe";
              Environment = [
                "HOME=/home/probe"
                "MOZ_HEADLESS=1"
                "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1"
              ];
              # The first-install restart drops Marionette; systemd relaunches
              # Firefox, which then finds the CSS current and keeps listening.
              Restart = "always";
              RestartSec = 2;
              # SIGTERM to Firefox alone, so it can shut down cleanly and write
              # prefs.js before its content processes go away.
              KillMode = "mixed";
              TimeoutStopSec = 60;
            };
            script = ''
              exec /run/current-system/sw/bin/firefox --marionette -remote-allow-system-access --new-instance --profile ${profile} about:blank
            '';
          };
          systemd.services.verify-firefox-probe = {
            path = [
              pkgs.coreutils
              pkgs.gnugrep
              pkgs.systemd
            ];
            wantedBy = [ "multi-user.target" ];
            after = [
              "firefox-probe.service"
              "tmp-shared.mount"
            ];
            serviceConfig.Type = "oneshot";
            script = ''
              shared=/tmp/shared
              fail() {
                echo "$1" > $shared/failure
                echo FAIL > $shared/result
              }
              diagnostics() {
                {
                  echo "== firefox-probe NRestarts/Result"
                  systemctl show -p NRestarts -p Result firefox-probe
                  echo "== ls chrome"
                  ls -la ${profile}/chrome
                  echo "== ls profile"
                  ls -la ${profile}
                  echo "== journal"
                  journalctl -u seed-probe-profile -u firefox-probe --no-pager -o cat
                } > $shared/firefox.log 2>&1
                cp ${profile}/prefs.js $shared/prefs.js 2>/dev/null || true
              }
              ready() {
                test -s ${profile}/chrome/userChrome.css &&
                  test -s ${profile}/chrome/userContent.css &&
                  test -s ${profile}/chrome/textfox-installed.json &&
                  grep -q '0100007F:0B0C .* 0A ' /proc/net/tcp
              }
              # Ready for five seconds in a row, so the probe never reaches the
              # first process just before its install restart.
              steady=0
              for i in $(seq 1 120); do
                if ready; then steady=$((steady + 1)); else steady=0; fi
                [ "$steady" -ge 5 ] && break
                sleep 1
              done
              if [ "$steady" -lt 5 ]; then
                fail 'chrome files or Marionette never became ready'
                diagnostics
                systemctl poweroff
                exit 1
              fi
              if ! ${pkgs.python3}/bin/python3 ${./probe-vm-preferences.py} \
                --profile ${profile} --border 11px --expect-nova default \
                --expect-prefs '{"tf.test.sentinel":true,"browser.shell.checkDefaultBrowser":false,"textfox.nova.cleared":true}'; then
                fail 'live preference probe failed'
              elif [ -e ${profile}/user.js ]; then
                fail 'user.js exists'
              elif [ "$(cat ${profile}/unrelated.txt)" != '${unrelated}' ]; then
                fail 'unrelated.txt changed'
              else
                # Graceful stop: Firefox writes prefs.js on the way out.
                systemctl stop firefox-probe
                if ${pkgs.python3}/bin/python3 - ${profile}/prefs.js $shared/preferences.json > $shared/persisted.json <<'EOF'
              import json, re, sys
              text = open(sys.argv[1]).read()
              live = json.load(open(sys.argv[2]))['preferences']
              user = {m[0]: m[1] for m in re.findall(r'user_pref\("([^"]+)",\s*(true|false)\);', text)}
              nova = 'browser.nova.enabled'
              result = {'sentinel': user.get('tf.test.sentinel'), 'nova_line': user.get(nova),
                        'marker': user.get('textfox.nova.cleared'), 'nova_default': live[nova]['default']}
              print(json.dumps(result))
              sys.exit(0 if user.get('tf.test.sentinel') == 'true' and nova not in user
                       and user.get('textfox.nova.cleared') == 'true' else 1)
              EOF
                then
                  echo PASS > $shared/result
                else
                  fail 'prefs.js after graceful stop: sentinel or marker missing, or a Nova line kept'
                fi
              fi
              diagnostics
              systemctl poweroff
            '';
          };
        }
      )
    ];
  };
in
system.config.system.build.vm
