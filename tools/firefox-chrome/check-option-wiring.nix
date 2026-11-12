{ nixpkgsPath, variants }:
let
  lib = import (nixpkgsPath + "/lib");
  module = ../../nix/modules/options.nix;

  assertionsStub =
    { lib, ... }:
    {
      options.assertions = lib.mkOption {
        type = lib.types.listOf lib.types.unspecified;
        default = [ ];
      };
    };

  evaluate =
    overrides:
    let
      evaluated = lib.evalModules {
        modules = [
          assertionsStub
          module
          { textfox.config = overrides; }
        ];
      };
    in
    {
      inherit (evaluated.config.textfox) configCss configPrefs;
    };

  walk =
    prefix: attrs:
    if (attrs._type or "") == "option" then
      let
        visible = attrs.visible or true;
      in
      [
        (
          {
            path = prefix;
            inherit visible;
          }
          // lib.optionalAttrs visible { inherit (attrs) default; }
        )
      ]
    else
      lib.concatMap (name: walk (prefix ++ [ name ]) attrs.${name}) (lib.attrNames attrs);

  base = lib.evalModules {
    modules = [
      assertionsStub
      module
    ];
  };
  baseline = evaluate { };
in
{
  paths = walk [ ] base.options.textfox.config;
  baseline = {
    inherit (baseline) configCss configPrefs;
  };
  results = map (
    variant:
    {
      inherit (variant) path value;
    }
    // evaluate (lib.setAttrByPath variant.path variant.value)
  ) variants;
}
