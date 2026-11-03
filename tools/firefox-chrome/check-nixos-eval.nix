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
assert lib.hasInfix ''pref("browser.nova.enabled", false);''
  (builtins.head matches).passthru.capturedPrefs;
assert lib.hasInfix "refusing to replace an existing profile chrome directory"
  (builtins.head matches).passthru.capturedPrefs;
{
  inherit (pkgs.firefox-unwrapped) version;
  autoconfigDrv = (builtins.head matches).drvPath;
  configuredBorder = evaluated.config.textfox.config.border.width;
}
