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
// <<< textfox managed prefs <<<
