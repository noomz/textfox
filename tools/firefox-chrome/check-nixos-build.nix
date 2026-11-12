{ nixpkgsPath }:
let
  pkgs = import nixpkgsPath { };
  lib = pkgs.lib;
  system = pkgs.stdenv.hostPlatform.system;
  wrap = import ../../nix/pkgs/wrapTextfox.nix {
    inherit lib;
    wrapFirefox = browser: args: pkgs.writeText "textfox-autoconfig-test.js" args.extraPrefs;
    inherit (pkgs) runCommandLocal;
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
        textfox.config.font.family = "monospace";
        textfox.config.tabs.vertical.enable = false;
        textfox.config.extraConfig = ''
          :root { --tf-border-width: 9px; }
        '';
        textfox.extraUserChrome = ''
          :root { --tf-border-width: 11px; }
          #nav-bar { --tf-extra-chrome-sentinel: applied; }
        '';
        textfox.extraUserContent = ''
          :root { --tf-border-width: 13px; }
        '';
        system.stateVersion = "26.05";
      }
    ];
  };
  matches = builtins.filter (
    p: lib.hasSuffix "-textfox-autoconfig-test.js" (toString p)
  ) evaluated.config.environment.systemPackages;
in
assert builtins.length matches == 1;
builtins.head matches
