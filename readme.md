
```
   __            __  ____          
  / /____  _  __/ /_/ __/___  _  __
 / __/ _ \| |/_/ __/ /_/ __ \| |/_/
/ /_/  __/>  </ /_/ __/ /_/ />  <  
\__/\___/_/|_|\__/_/  \____/_/|_|  
```

_a firefox theme for the tui enthusiast_

## Preview

![image](https://github.com/adriankarlen/textfox/blob/main/misc/vertical-tabs.png)

![image](https://github.com/adriankarlen/textfox/blob/main/misc/horizontal-tabs.png)

> [!NOTE]
> The color scheme used in the pictures is [Rosé Pine Moon](https://github.com/rose-pine/firefox).
> `textfox` tries to not hard code any colors, [Firefox Color extension](https://addons.mozilla.org/en-US/firefox/addon/firefox-color/) is the
> recommended approach to coloring Firefox with `textfox`.

## Installation

> [!IMPORTANT]
> **Updating textfox turns Firefox's new "Nova" UI on.** Earlier versions kept
> Nova off, and Firefox saved that in each profile. The update resets that saved
> setting once per profile, so Firefox's default (Nova on) applies again. This
> happens even if you turned Nova off yourself, for example because an earlier
> version of this README told you to: the reset cannot tell your choice from
> textfox's. With Home Manager it also applies to every profile listed in
> `textfox.profiles`, including ones textfox never ran in before.
>
> To keep the classic UI, set `browser.nova.enabled` to `false` in
> `about:config` after updating and restart Firefox. textfox keeps that choice
> from then on.

> [!NOTE]
> textfox supports Firefox's new "Nova" UI as well as the classic one, and no
> longer sets `browser.nova.enabled`. Nova is checked on Firefox 159 and 160 with
> textfox's default colors and a custom palette: on Linux for the toolbars, tabs,
> sidebar, split view, private windows, menus, the extensions and all-tabs
> panels, the new tab and `about:` pages, plus keyboard focus, right-to-left and
> reduced motion; on macOS and Windows for the navigation bar, URL bar, tabs,
> menus, findbar and new tab. Firefox themes from the theme picker (Light,
> Dark, Alpenglow and the Nova themes) are checked on Linux for the same
> surfaces and the section titles: textfox takes the theme's frame color as its
> background and paints every box with it, so the theme's toolbar color and
> background images do not show. Other panels, themes on macOS and Windows, and
> GPU rendering are not checked yet.
>
> Earlier textfox versions turned Nova off. Updating gives Firefox's own setting
> back once:
>
> - Installation script: run `sh tf-install.sh` again with Firefox closed. The
>   reset needs `python3`. Without it, the script tells you to reset
>   `browser.nova.enabled` in `about:config` yourself.
> - Home Manager: the next activation resets it. If Firefox is running, it
>   warns and resets on a later activation.
> - NixOS: Firefox resets it on its next start.
> - Manual copy: copy the new `user.js` first (the old one sets Nova off on every
>   start), then close Firefox and run `python3 tools/clear-nova-pref.py <profile>`,
>   or reset `browser.nova.enabled` in `about:config`. The script needs macOS or
>   Linux; on Windows, use `about:config`.
>
> The reset happens once per profile (see the note above to keep the classic
> UI).

### Installation script

1. Download/clone the repo.
2. Inside the download run `sh tf-install.sh` and follow the script
   instructions.

> [!IMPORTANT]
> This script automates file writes, use with caution.

> [!NOTE]
> The installation script copies to contents of the repos `chrome` directory to
> the path specified, this way your `config.css` or any other `css`-files not
> part of the repo will be kept. It merges textfox's settings into `user.js`
> between the `textfox managed prefs` markers, so your own preferences in that
> file keep working; keep them outside the markers.

### Manual

1. Download the files
2. Go to `about:profiles`
3. Find the names of target profiles (ex: `Profile: Default`)
4. Open the profile's root directory
5. Move the files chrome directory and user.js there
6. Restart Firefox

> [!IMPORTANT]
> textfox now supports horizontal tabs, to enable them change the
> `--tf-display-horizontal-tabs` variable in your `config.css` to `block`. See
> [CSS configurations](#css-configurations) for more info.

> [!NOTE]
> If you don't want to use the provided user.js, please read through it and
> apply the settings in `about:config` manually. These are needed for the css to
> work.

### Nix

This repo includes a Nix flake that exposes a home-manager module that installs textfox.

To enable the module, add the repo as a flake input, import the module, and enable textfox.

<details><summary>Install using your home-manager module defined within your `nixosConfigurations`:</summary>

```nix

  # flake.nix

  {

      inputs = {
         # ---Snip---
         home-manager = {
           url = "github:nix-community/home-manager";
           inputs.nixpkgs.follows = "nixpkgs";
         };

         textfox.url = "github:adriankarlen/textfox";
         # ---Snip---
      }

      outputs = {nixpkgs, home-manager, ...} @ inputs: {
          nixosConfigurations.HOSTNAME = nixpkgs.lib.nixosSystem {
            specialArgs = { inherit inputs; };
            modules = [
            home-manager.nixosModules.home-manager
              {
               # Must pass in inputs so we can access the module
                home-manager.extraSpecialArgs = {
                  inherit inputs;
                };
              }
           ];
        };
     }
  }
```
```nix

# home.nix

imports = [ inputs.textfox.homeManagerModules.default ];

textfox = {
    enable = true;
    # Replace with the names of profiles, defined in home-manager, or find existing ones in `about:profiles`
    profiles = ["profile_1" "profile_2"];
    config = {
        # Optional config
    };
};
```
</details>

<details><summary>Install using `home-manager.lib.homeManagerConfiguration`:</summary>

```nix

  # flake.nix

  {
    inputs = {
       # ---Snip---
       home-manager = {
         url = "github:nix-community/home-manager";
         inputs.nixpkgs.follows = "nixpkgs";
       };

       textfox.url = "github:adriankarlen/textfox";
       # ---Snip---
    }

    outputs = {nixpkgs, home-manager, textfox ...}: {
        homeConfigurations."user@hostname" = home-manager.lib.homeManagerConfiguration {
            pkgs = nixpkgs.legacyPackages.x86_64-linux;

            modules = [
                textfox.homeManagerModules.default
                # ...
            ];
        };
    };
  }
```
  ```nix

  # home.nix

  textfox = {
      enable = true;
      # Replace with the names of profiles, defined in home-manager, or find existing ones in `about:profiles`
      profiles = ["profile_1" "profile_2"];
      config = {
          # Optional config
      };
  };
  ```
</details>

<details><summary>Configuration options:</summary>

All configuration options are optional and can be set as this example shows (real default values [can be found below](#defaults)):

```nix

  textfox = {
      enable = true;
      # Replace with the names of profiles, defined in home-manager, or find existing ones in `about:profiles`
      profiles = ["profile_1" "profile_2"];
      config = {
        background = {
          color = "#123456";
        };
        border = {
          color = "#654321";
          width = "4px";
          transition = "1.0s ease";
          radius = "3px";
        };
        displayWindowControls = true;
        displayNavButtons = true;
        displayUrlbarIcons = true;
        displaySidebarTools = false;
        displayTitles = false;
        containerFrame = true;
        newtabLogo = "   __            __  ____          \A   / /____  _  __/ /_/ __/___  _  __\A  / __/ _ \\| |/_/ __/ /_/ __ \\| |/_/\A / /_/  __/>  </ /_/ __/ /_/ />  <  \A \\__/\\___/_/|_|\\__/_/  \\____/_/|_|  ";
        font = {
          family = "Fira Code";
          size = "15px";
          accent = "#654321";
          color = "var(--lwt-text-color)";
        };
        tabs = {
          horizontal.enable = true;
          vertical.enable = true;
        };
        navbar = {
          margin = "8px 8px 2px";
          padding = "4px";
        };
        bookmarks = {
          alignment = "left";
        };
        icons = {
          toolbar.extensions.enable = true;
          context.extensions.enable = true;
          context.firefox.enable = true;
        };
        textTransform = "uppercase";
        extraConfig = "/* custom css here */";
      };
  };
```
</details>

<details><summary>NixOS module (`nixosModules.default`): when textfox refuses to install</summary>

The NixOS module wraps Firefox and copies textfox into the profile's `chrome/`
directory on every start. It only replaces files it can prove it wrote. If
`chrome/userChrome.css` or `chrome/userContent.css` holds anything else (your
own files, files you edited after installation, or an older textfox install
whose Nix store path was garbage-collected), it changes nothing and shows no
message on screen. Your old styles stay in place.

To let textfox take over, either move those two files out of `chrome/`, or set
`textfox.force-adopt` to `true` in `about:config` and restart Firefox. With
`textfox.force-adopt` on, textfox copies the files into a
`chrome/textfox-backup-*` directory before replacing them. Set it back to
`false` afterwards, so later edits of your own are refused instead of replaced.
</details>

## Uninstallation

### Uninstall script

1. Inside the cloned repo run `sh tf-uninstall.sh` and follow the script
   instructions.

> [!IMPORTANT]
> This script automates file removal, use with caution.

> [!NOTE]
> The install script names each backup after whose files it holds:
> `chrome-pre-textfox-YYYYMMDD_HHMMSS.bak` holds your files from before textfox,
> `chrome-textfox-YYYYMMDD_HHMMSS.bak` the previous textfox files a reinstall
> replaced. The uninstall script offers to restore the newest pre-textfox backup
> (or, for profiles installed by older scripts, the oldest
> `chrome-YYYYMMDD_HHMMSS.bak`), never a backup of textfox's own files, and puts
> its files back beside anything you kept in `chrome/`. It removes only the files textfox ships from `chrome/`, so
> your `chrome/config.css` and any files of your own stay, and it removes the
> `chrome` directory only when nothing else is left in it. It removes only the
> `textfox managed prefs` block from `user.js`, and deletes that file only when
> nothing else was in it.

### Manual

1. Go to `about:profiles`
2. Open the root directory of the profile textfox was installed to
3. Delete the `chrome` directory (keep a copy of `chrome/config.css` first if
   you want your configuration back later)
4. Remove the `textfox managed prefs` block from `user.js`, or delete the file
   if it holds nothing else
5. Restart Firefox

> [!NOTE]
> If you had a previous `chrome` directory before installing textfox, the
> install script moved the files textfox replaced into
> `chrome-pre-textfox-YYYYMMDD_HHMMSS.bak` (`chrome-YYYYMMDD_HHMMSS.bak` with
> older scripts). You can restore them by moving them back into
> `chrome`.

## Customization

The icon configuration utilizes code that is originally from ShyFox, therefore
the same settings are used. With a manual install, set them in about:config.
With either Nix module, set them through `textfox.config.icons` instead: the
module writes all three preferences on every Firefox start, so a value changed
in about:config is reset at the next start.
| Setting | true | false (default) |
| -------------------------------------- | --------------------------------------------------------------------- | ------------------------- |
| `shyfox.enable.ext.mono.toolbar.icons` | Supported extensions get monochrome icons as toolbar buttons | Standard icons used |
| `shyfox.enable.ext.mono.context.icons` | Supported extensions get monochrome icons as context menu items | Standard icons used |
| `shyfox.enable.context.menu.icons` | Many context menu items get icons | No icons in context menus |

### CSS configurations
The theme ships with a `defaults.css`, this file can be overridden by creating a
`config.css` inside the chrome directory.

#### Defaults
```css
:root {
  --tf-font-family: "SF Mono", Consolas, monospace; /* Font family of config */
  --tf-font-size: 14px; /* Font size of config */
  --tf-accent: var(--toolbarbutton-icon-fill); /* Accent color used, eg: color when hovering a container  */
  --tf-bg: var(--lwt-accent-color, -moz-dialog); /* Background color of all elements, tab colors derive from this */
  --tf-fg: contrast-color(var(--tf-bg)); /* Text color on --tf-bg; black or white, whichever reads better. var(--lwt-text-color) keeps the theme's text color */
  --tf-border: color-mix(in srgb, var(--tf-fg) 20%, var(--tf-bg)); /* Border color when not hovered */
  --tf-border-transition: 0.2s ease; /* Smooth color transitions for borders */
  --tf-border-width: 2px; /* Width of borders */
  --tf-rounding: 0px; /* Border radius used through out the config */
  --tf-text-transform: none; /* Text transform to use */
  --tf-display-horizontal-tabs: none; /* If horizontal tabs should be shown, none = hidden, block = shown */
  --tf-display-vertical-tabs: flex; /* If vertical tabs should be shown, none = hidden, flex = shown */
  --tf-display-window-controls: none; /* If the window controls should be shown (won't work with sidebery and hidden horizontal tabs), none = hidden, flex = shown */ 
  --tf-display-nav-buttons: none; /* If the navigation buttons (back, forward) should be shown, none = hidden, flex = shown */
  --tf-display-urlbar-icons: none; /* If the icons inside the url bar should be shown, none = hidden, flex = shown */
  --tf-display-sidebar-tools: flex; /* If the "Customize sidebar" button on the sidebar should be shown, none = hidden, flex = shown */ 
  --tf-display-titles: flex; /* If titles (tabs, navbar, main etc.) should be shown, none = hidden, flex = shown */
  --tf-container-frame: 0; /* If the main and split-view frames take the container color of the tab they show, 0 = --tf-border, 1 = container color */
  --tf-newtab-logo: "   __            __  ____          \A   / /____  _  __/ /_/ __/___  _  __\A  / __/ _ \\| |/_/ __/ /_/ __ \\| |/_/\A / /_/  __/>  </ /_/ __/ /_/ />  <  \A \\__/\\___/_/|_|\\__/_/  \\____/_/|_|  ";
  --tf-navbar-margin: 8px 8px 2px; /* navbar margin */
  --tf-navbar-padding: 4px; /* navbar padding */
  --tf-bookmarks-alignment: center; /* alignment of bookmarks in the bookmarks toolbar (if you have many bookmarks, left is recommended) */
}

```

### Changing the new tab logo

The new tab logo can be any string you want, to create a string with line breaks
add a `\A` at every line break, also make sure to break any backslashes, eg. if
you want a `\`, you need to write `\\`. I used [this tool](https://www.patorjk.com/software/taag/#p=display&f=Slant&t=textfox)
to create the current logo.

Wanna hide the logo? Simply pass an empty string as the logo.

### Recipes

Here are some example changes that you can do to achieve different looks.

#### Swap positions of tabs and window controls when using horizontal tabs
```css
/* path: chrome/config.css */
:root {
  --tf-display-horizontal-tabs: inline-flex;
  --tf-display-window-controls: flex;
}
```

#### Rounded borders
```css
/* path: chrome/config.css */
:root {
  --tf-rounding: 4px;
}
```

#### Align bookmarks to the left
```css
/* path: chrome/config.css */
:root {
  --tf-bookmarks-alignment: left;
}
```

#### Adjust title margins

The titles (e.g. "tabs", "navbar", "main") are `::before` pseudo-elements positioned on the border of each container. Override the margin per selector to reposition them.

```css
/* path: chrome/config.css */
/* These are just the default values, change them to fit your needs */

/* main title */
#tabbrowser-tabbox::before {
  margin: -1.75rem 0rem !important;
}

/* navbar title */
#nav-bar::before {
  margin: -16px 8px !important;
}

/* bookmarks title */
#PersonalToolbar::before {
  margin: -1.25rem 0.4rem !important;
}

/* tools title */
.buttons-wrapper::before {
  margin: -0.85rem 0.85rem !important;
}

/* tool title */
#sidebar-box::before {
  margin: -0.85rem 0.85rem !important;
}

/* vertical tabs title */
box#vertical-tabs::before {
  margin: -1.75rem .4rem !important;
}

/* horizontal tabs title */
#TabsToolbar::before {
  margin: -1rem .75rem !important;
}

/* findbar title */
findbar::before {
  margin: -1.75rem .75rem !important;
}

/* split view panel title */
.split-view-panel::before {
  margin: -1.75rem 0rem !important;
}
```

#### Do you have a banger recipe?
Feel free to open a PR and add it here!

## Tooling

The repo ships a small toolkit under `tools/firefox-chrome/` (also
symlinked into `.claude/skills/` so AI agents pick it up automatically). It
exists to solve the hardest part of maintaining a userChrome theme: Firefox's
internal UI is real DOM with its own CSS, and the selectors/IDs change between
versions — the number one cause of theme breakage.

These scripts are plain shell/Python and work on their own, no agent required.

### `sf-search.sh` — find real selectors in the latest Firefox

Searches the latest Firefox release source (via searchfox) so you can confirm a
selector actually exists before writing CSS against it.

```sh
# Where is .urlbar-background styled?
sh tools/firefox-chrome/sf-search.sh "urlbar-background" "*.css"

# Find an element in the browser DOM
sh tools/firefox-chrome/sf-search.sh "nav-bar" "*.xhtml"
```

Output is `path:line: matched line`. Zero matches for a selector textfox already
uses is a strong hint that Firefox renamed or removed it.

### `firefox-live.sh` — inspect the live browser UI

Launches a throwaway Firefox (with textfox applied) and inspects the actual
rendered chrome DOM, then cleans up after itself. Handy on its own to identify
an element's real id/classes, its runtime state attributes (`[open]`,
`[focused]`, …), and the computed styles that won the cascade.

```sh
# Inspect the nav bar
sh tools/firefox-chrome/firefox-live.sh nav-bar

# A specific selector + the computed properties you care about
sh tools/firefox-chrome/firefox-live.sh --selector "#urlbar .urlbar-background" \
   -- --computed background-color,border,box-shadow

# Bare Firefox, without textfox applied
sh tools/firefox-chrome/firefox-live.sh --vanilla nav-bar
```

> [!NOTE]
> Requires `python3` and a Firefox install. The first run creates a small
> virtualenv at `~/.cache/textfox-firefox-live/venv` for `marionette_driver`.
> Live inspection needs a display; on headless Linux prefix the command with
> `xvfb-run -a`.

## Community

Join [#textfox:matrix.org](https://matrix.to/#/#textfox:matrix.org) for support and discussion.

## Acknowledgements

[Naezr](https://github.com/Naezr) - Icon logic and some sidebery logic.
