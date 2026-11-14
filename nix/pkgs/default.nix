{ stdenv, fetchFromGithub, ... }:

stdenv.mkDerivation {
  pname = "textfox";
  version = "git";

  src = ../../.;

  installPhase = ''
    mkdir -p $out/chrome
    cp -r chrome/* $out/chrome
    cp user.js $out/user.js
    cp tools/clear-nova-pref.py $out/clear-nova-pref.py
  '';
}
