// >>> textfox managed prefs >>>
// The install script rewrites this block and keeps everything outside it. Put
// your own preferences outside the markers so a reinstall cannot remove them.

// userchrome.css usercontent.css activate
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);

// Fill SVG Color
user_pref("svg.context-properties.content.enabled", true);

// CSS's `:has()` selector
user_pref("layout.css.has-selector.enabled", true);

// Disable the new Firefox "Nova" UI (textfox does not support it yet)
user_pref("browser.nova.enabled", false);

// macOS: keep context menus as Firefox popups. The OS-drawn native menu ignores
// userChrome.css, so textfox's menu styling and icons would never show.
user_pref("widget.macos.native-context-menus", false);
// <<< textfox managed prefs <<<
