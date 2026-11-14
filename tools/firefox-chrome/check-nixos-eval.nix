{ nixpkgsPath }:
let
  pkgs = import nixpkgsPath { };
  lib = pkgs.lib;
  system = pkgs.stdenv.hostPlatform.system;
  wrap = import ../../nix/pkgs/wrapTextfox.nix {
    inherit lib;
    wrapFirefox =
      browser: args:
      (pkgs.writeText "textfox-autoconfig-test.js" args.extraPrefs)
      // {
        passthru.capturedPrefs = args.extraPrefs;
      };
    runCommandLocal =
      name: attrs: script:
      "/nix/store/00000000000000000000000000000000-" + name;
  };
  module = import ../../nix/modules/nixos.nix {
    self.packages.${system}.wrapTextfox = wrap;
  };
  evaluated = import (nixpkgsPath + "/nixos/lib/eval-config.nix") {
    inherit system;
    modules = [
      module
      {
        textfox.enable = true;
        textfox.config.border.width = "7px";
        textfox.config.newtabLogo = "say \"hi\"\\path\n1next\rtest\n";
        textfox.config.icons.context.firefox.enable = true;
        system.stateVersion = "26.05";
      }
    ];
  };
  matches = builtins.filter (
    p: lib.hasSuffix "-textfox-autoconfig-test.js" (toString p)
  ) evaluated.config.environment.systemPackages;
in
assert builtins.length matches == 1;
assert lib.hasInfix "--tf-border-width: 7px;" evaluated.config.textfox.configCss;
assert lib.hasInfix ''--tf-newtab-logo: "say \"hi\"\\path\A 1next\D test\A ";''
  evaluated.config.textfox.configCss;
# T25: Nova is no longer assigned; the persisted false is cleared once.
assert
  !(lib.hasInfix ''pref("browser.nova.enabled"'' (builtins.head matches).passthru.capturedPrefs);
assert lib.hasInfix ''clearUserPref("browser.nova.enabled")''
  (builtins.head matches).passthru.capturedPrefs;
assert lib.hasInfix "textfox: refusing to install" (builtins.head matches).passthru.capturedPrefs;
assert lib.hasInfix ''pref("shyfox.enable.context.menu.icons", true);''
  (builtins.head matches).passthru.capturedPrefs;
assert lib.hasInfix ''pref("shyfox.enable.ext.mono.toolbar.icons", false);''
  (builtins.head matches).passthru.capturedPrefs;
{
  inherit (pkgs.firefox-unwrapped) version;
  autoconfigDrv = (builtins.head matches).drvPath;
  configuredBorder = evaluated.config.textfox.config.border.width;
  generatedCss = evaluated.config.textfox.configCss;
}
