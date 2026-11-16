{
  lib,
  config,
  ...
}:
let
  inherit (lib.options) mkOption mkEnableOption;
  inherit (lib.modules) mkRenamedOptionModule;
  inherit (lib.modules) mkRemovedOptionModule;
  inherit (lib.strings) replaceStrings;
  inherit (lib.types) attrsOf bool str;

  cfg = config.textfox.config;
in
{
  imports = [
    (mkRenamedOptionModule
      [ "textfox" "config" "displayHorizontalTabs" ]
      [ "textfox" "config" "tabs" "horizontal" "enable" ]
    )
    (mkRemovedOptionModule [
      "textfox"
      "config"
      "tabs"
      "vertical"
      "sidebery"
    ] "Sidebery support was dropped in textfox.")
  ];

  options.textfox = {
    enable = mkEnableOption "Textfox.";

    config = {
      displayWindowControls = mkEnableOption "window controls.";
      displayNavButtons = mkEnableOption "back and forward navigation buttons in the Firefox UI.";
      displayUrlbarIcons = mkEnableOption "icons inside url bar.";
      displaySidebarTools = mkEnableOption "sidebar tools section." // {
        default = true;
      };

      displayTitles = mkOption {
        type = bool;
        default = true;
        description = "If titles (tabs, navbar, main etc.) should be shown";
      };

      containerFrame = mkEnableOption "frames that follow the container color of the tab they show (content box and split-view panels)";

      themeGradient = mkEnableOption "browser Nova's own window gradient behind the window, without a Firefox theme (macOS and Windows; Linux paints it transparent)";

      themeImages = mkEnableOption "a Firefox theme's images and gradients behind the window";

      themeScrim = mkOption {
        type = str;
        default = "80%";
        description = "How much the background color washes over theme art when themeGradient or themeImages is on (0% shows the art at full strength, 100% hides it). Below 80% text may fall under 4.5:1 contrast on bright art.";
      };

      newtabLogo = mkOption {
        type = str;
        default = "   __            __  ____          \n  / /____  _  __/ /_/ __/___  _  __\n / __/ _ \\| |/_/ __/ /_/ __ \\| |/_/\n/ /_/  __/>  </ /_/ __/ /_/ />  <  \n\\__/\\___/_/|_|\\__/_/  \\____/_/|_|  ";
        apply = p: replaceStrings [ "\n" "\r" "\\" "\"" ] [ "\\A " "\\D " "\\\\" "\\\"" ] p;
        description = "ASCII logo used for new tab page";
      };

      background.color = mkOption {
        type = str;
        default = "var(--lwt-accent-color, -moz-dialog)";
        description = "Background color of all elements.";
      };

      font = {
        family = mkOption {
          type = str;
          default = "\"SF Mono\", Consolas, monospace";
          description = "Default font family";
        };
        size = mkOption {
          type = str;
          default = "14px";
          description = "The font size to use";
        };
        accent = mkOption {
          type = str;
          default = "var(--toolbarbutton-icon-fill)";
          description = "The accent color to use";
        };
        color = mkOption {
          type = str;
          default = "contrast-color(var(--tf-bg))";
          description = "Text color on the background color. The default picks black or white, whichever reads better; \"var(--lwt-text-color)\" keeps the theme's text color.";
        };
      };

      border = {
        color = mkOption {
          type = str;
          default = "color-mix(in srgb, var(--tf-fg) 20%, var(--tf-bg))";
          description = "Border color when not hovered";
        };
        transition = mkOption {
          type = str;
          default = "0.2s ease";
          description = "Color transitions for borders";
        };
        width = mkOption {
          type = str;
          default = "2px";
          description = "Width of borders";
        };
        radius = mkOption {
          type = str;
          default = "0px";
          description = "Border radius used throughout the config";
        };
      };

      icons = {
        toolbar.extensions.enable = mkEnableOption "monochrome extension toolbar buttons.";
        context.extensions.enable = mkEnableOption "monochrome extension context menu items.";
        context.firefox.enable = mkEnableOption "icons for common context menu items.";
      };

      tabs = {
        horizontal.enable = mkEnableOption "display of horizontal tabs.";
        vertical.enable = mkEnableOption "display of vertical tabs." // {
          default = true;
        };
      };

      extraConfig = mkOption {
        type = str;
        default = "";
        description = "Extra lines to add to config.css";
      };

      textTransform = mkOption {
        type = str;
        default = "none";
        description = "Text transform to use";
      };

      navbar = {
        margin = mkOption {
          type = str;
          default = "8px 8px 2px";
          description = "Navbar margin";
        };
        padding = mkOption {
          type = str;
          default = "4px";
          description = "Navbar padding";
        };
      };

      bookmarks.alignment = mkOption {
        type = str;
        default = "center";
        description = "Alignment of bookmarks in the bookmarks toolbar (if you have many bookmarks, left is recommended)";
      };
    };

    configPrefs = mkOption {
      readOnly = true;
      visible = false;
      type = attrsOf bool;
      description = "Firefox preferences the icon options set; both Nix modules consume this.";
      default = {
        "shyfox.enable.ext.mono.toolbar.icons" = cfg.icons.toolbar.extensions.enable;
        "shyfox.enable.ext.mono.context.icons" = cfg.icons.context.extensions.enable;
        "shyfox.enable.context.menu.icons" = cfg.icons.context.firefox.enable;
      };
    };

    configCss = mkOption {
      readOnly = true;
      visible = false;
      type = str;
      default = ''
        :root {
          --tf-font-family: ${cfg.font.family};
          --tf-font-size: ${cfg.font.size};
          --tf-accent: ${cfg.font.accent};
          --tf-bg: ${cfg.background.color};
          --tf-fg: ${cfg.font.color};
          --tf-border: ${cfg.border.color};
          --tf-border-transition: ${cfg.border.transition};
          --tf-border-width: ${cfg.border.width};
          --tf-rounding: ${cfg.border.radius};
          --tf-text-transform: ${cfg.textTransform};
          --tf-display-horizontal-tabs: ${if cfg.tabs.horizontal.enable then "block" else "none"};
          --tf-display-vertical-tabs: ${if cfg.tabs.vertical.enable then "flex" else "none"};
          --tf-display-window-controls: ${if cfg.displayWindowControls then "flex" else "none"};
          --tf-display-nav-buttons: ${if cfg.displayNavButtons then "flex" else "none"};
          --tf-display-urlbar-icons: ${if cfg.displayUrlbarIcons then "flex" else "none"};
          --tf-display-sidebar-tools: ${if cfg.displaySidebarTools then "flex" else "none"};
          --tf-display-titles: ${if cfg.displayTitles then "flex" else "none"};
          --tf-container-frame: ${if cfg.containerFrame then "1" else "0"};
          --tf-theme-gradient: ${if cfg.themeGradient then "1" else "0"};
          --tf-theme-images: ${if cfg.themeImages then "1" else "0"};
          --tf-theme-scrim: ${cfg.themeScrim};
          --tf-newtab-logo: "${cfg.newtabLogo}";
          --tf-navbar-margin: ${cfg.navbar.margin};
          --tf-navbar-padding: ${cfg.navbar.padding};
          --tf-bookmarks-alignment: ${cfg.bookmarks.alignment};
        }
        ${cfg.extraConfig}
      '';
    };
  };
}
