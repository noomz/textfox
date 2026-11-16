{
  nixpkgsPath,
  configCss ? ":root { --tf-border-width: 11px; }",
}:
let
  pkgs = import nixpkgsPath { system = "x86_64-linux"; };
  wrap = pkgs.callPackage ../../nix/pkgs/wrapTextfox.nix { };
in
wrap pkgs.firefox-unwrapped {
  inherit configCss;
}
