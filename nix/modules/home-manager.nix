inputs:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (pkgs.stdenv.hostPlatform) system;
  package = inputs.self.packages.${system}.default;
  configDir =
    "${config.programs.firefox.configPath}/"
    + (lib.optionalString pkgs.stdenv.hostPlatform.isDarwin "Profiles/");

  cfg = config.textfox;
in
{

  imports = [
    ./options.nix
    (lib.mkChangedOptionModule [ "textfox" "profile" ] [ "textfox" "profiles" ] (
      config:
      let
        profile = lib.getAttrFromPath [ "textfox" "profile" ] config;

      in
      [ profile ]
    ))
  ];

  options.textfox = {
    profiles = lib.mkOption {
      type = with lib.types; listOf str;
      default = [ ];
      description = "List of Firefox profiles to apply the textfox configuration to";
    };
  };

  config = lib.mkIf cfg.enable {
    programs.firefox = {
      enable = true;
      profiles = lib.mkMerge (
        map (profile: {
          "${profile}" = {
            extraConfig = builtins.readFile "${package}/user.js";
            settings = cfg.configPrefs;
            containersForce = true;
            userChrome = lib.mkBefore (builtins.readFile "${package}/chrome/userChrome.css");
          };
        }) cfg.profiles
      );
    };

    home.file = lib.mkMerge (
      map (profile: {
        "${configDir}${profile}/chrome" = {
          source = pkgs.lib.cleanSourceWith {
            src = "${package}/chrome";
            filter = path: type: !(type == "regular" && baseNameOf path == "userChrome.css");
          };
          recursive = true;
        };
        "${configDir}${profile}/chrome/config.css" = {
          text = cfg.configCss;
        };
      }) cfg.profiles
    );

    # Earlier textfox generations assigned browser.nova.enabled false through
    # user.js, and Firefox keeps that in prefs.js after the assignment is gone.
    # Clear it once per profile, after the retired user.js is linked. Exit 75
    # means Firefox holds the profile; the marker is written only on success, so
    # the next activation tries again.
    home.activation.textfoxClearNova = lib.hm.dag.entryAfter [ "linkGeneration" ] (
      lib.concatMapStrings (profile: ''
        textfoxStatus=0
        run ${pkgs.python3}/bin/python3 ${package}/clear-nova-pref.py ${lib.escapeShellArg "${config.home.homeDirectory}/${configDir}${profile}"} || textfoxStatus=$?
        if [ "$textfoxStatus" -eq 75 ]; then
          warnEcho ${lib.escapeShellArg "textfox: Firefox is using profile ${profile}; browser.nova.enabled is reset on the next activation"}
        elif [ "$textfoxStatus" -ne 0 ]; then
          warnEcho ${lib.escapeShellArg "textfox: could not reset browser.nova.enabled in profile ${profile}"} "(exit $textfoxStatus)"
        fi
      '') cfg.profiles
    );
  };
}
