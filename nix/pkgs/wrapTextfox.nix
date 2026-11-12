{
  lib,
  wrapFirefox,
  runCommandLocal,
}:
browser:
{
  configCss ? "",
  extraUserChrome ? "",
  extraUserContent ? "",
  ...
}@args:
let

  chromeSrc = ./../../chrome;

  textfoxChrome =
    runCommandLocal "textfox-chrome"
      {
        inherit configCss extraUserChrome extraUserContent;
        passAsFile = [
          "configCss"
          "extraUserChrome"
          "extraUserContent"
        ];

        src = chromeSrc;
      }
      ''
        mkdir -p "$out"

        # Asset set identity, hashed inside the build: no derivation output is
        # read during evaluation (that would be import-from-derivation), and the
        # generated CSS can name the directory it is written into. The installed
        # profile keeps a directory of the same name, so an installed profile
        # never depends on a store path that garbage collection is free to delete.
        assetsDigest=$(
          cd "$src/icons"
          find . -type f | sort | while read -r icon; do
            printf '%s %s\n' "$icon" "$(sha256sum < "$icon" | cut -d" " -f1)"
          done | sha256sum | cut -c1-8
        )
        assetsDirName="textfox-assets-$assetsDigest"
        mkdir -p "$out/$assetsDirName"
        cp -R "$src/icons/." "$out/$assetsDirName/"

        ### USERCHROME
        cat "$src/overwrites.css" >> "$out/userChrome.css"
        cat "$src/userChrome.css" >> "$out/userChrome.css"
        cat "$src/sidebar.css" >> "$out/userChrome.css"
        cat "$src/browser.css" >> "$out/userChrome.css"
        cat "$src/findbar.css" >> "$out/userChrome.css"
        cat "$src/navbar.css" >> "$out/userChrome.css"
        cat "$src/urlbar.css" >> "$out/userChrome.css"
        sed "s|\./icons/|$assetsDirName/|g" "$src/icons.css" >> "$out/userChrome.css"
        cat "$src/menus.css" >> "$out/userChrome.css"
        cat "$src/tabs.css" >> "$out/userChrome.css"

        cat "$src/defaults.css" >> "$out/userChrome.css"
        cat "$configCssPath" >> "$out/userChrome.css"
        cat "$extraUserChromePath" >> "$out/userChrome.css"

        ### USERCONTENT
        cat "$src/content/newtab.css" >> "$out/userContent.css"
        cat "$src/content/about.css" >> "$out/userContent.css"

        cat "$src/defaults.css" >> "$out/userContent.css"
        cat "$configCssPath" >> "$out/userContent.css"
        cat "$extraUserContentPath" >> "$out/userContent.css"

        ### MANIFEST
        # The autoconfig reads target hashes from here at runtime. Baking them
        # into the autoconfig script instead would be an import-from-derivation
        # and circular anyway: the store path is already the trust anchor.
        {
          printf 'assetsDir %s\n' "$assetsDirName"
          printf 'css userChrome.css %s\n' "$(sha256sum < "$out/userChrome.css" | cut -d" " -f1)"
          printf 'css userContent.css %s\n' "$(sha256sum < "$out/userContent.css" | cut -d" " -f1)"
          (
            cd "$out/$assetsDirName"
            find . -type f | sed 's|^\./||' | sort | while read -r icon; do
              printf 'icon %s %s\n' "$icon" "$(sha256sum < "$icon" | cut -d" " -f1)"
            done
          )
        } > "$out/manifest.sha256"
      '';

  configScript = ''
    /* TEXTFOX GENERATED CONFIG */

    const {classes: Cc, interfaces: Ci, utils: Cu} = Components;
    var { FileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");

    // Protocol: textfox owns exactly the leaves named in its marker, plus the
    // staging names, the digest-named asset directories it created, and its own
    // marker. Everything else in the profile is somebody else's file: never
    // written, never removed. A file is only replaced when its current bytes are
    // either what textfox last wrote (an update) or what this store holds
    // (already current, or a crash mid-install). Anything else means a third
    // party made or edited that file, and the whole generation is refused.
    var PROTOCOL = 1;
    var MARKER_NAME = "textfox-installed.json";
    var CSS_NAMES = ["userChrome.css", "userContent.css"];
    var BACKUP_PREFIX = "textfox-backup-";
    var ASSETS_PREFIX = "textfox-assets-";
    var ASSETS_DIR_RE = new RegExp("^" + ASSETS_PREFIX + "[0-9a-f]{8}$");
    var STAGING_PREFIX = ".textfox-staging-";

    var chromeDir = Services.dirsvc.get("ProfD", Ci.nsIFile);
    chromeDir.append("chrome");

    // XP_UNIX forces symlinks to be resolved when copying, so we are just going
    // to normal copy from nix store.
    // <https://bugzilla.mozilla.org/show_bug.cgi?id=480726>
    var storeDir = new FileUtils.File("${textfoxChrome}");

    // Prefs first: the install block below can restart Firefox, and prefs must
    // not be skipped when it does.
    pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
    pref("svg.context-properties.content.enabled", true);
    pref("layout.css.has-selector.enabled", true);
    // textfox does not support the new Nova UI yet
    pref("browser.nova.enabled", false);
    // Opt-in escape hatch for a profile textfox cannot prove it owns: mismatched
    // owned files are copied aside, then converged. Off by default.
    var forceAdopt = false;
    try {
      forceAdopt = Services.prefs.getBoolPref("textfox.force-adopt", false);
    } catch (e) {}

    function child(dir, name) {
      var f = dir.clone();
      f.append(name);
      return f;
    }

    function entries(dir) {
      var out = [];
      var en = dir.directoryEntries;
      while (en.hasMoreElements()) {
        out.push(en.getNext().QueryInterface(Ci.nsIFile));
      }
      return out;
    }

    function sha256Hex(file) {
      var stream = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
      stream.init(file, 0x01, 0444, 0);
      var hasher = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
      hasher.init(Ci.nsICryptoHash.SHA256);
      hasher.updateFromStream(stream, -1);
      stream.close();
      var bin = hasher.finish(false);
      var hex = "";
      for (var i = 0; i < bin.length; i++) {
        var h = bin.charCodeAt(i).toString(16);
        hex += h.length < 2 ? "0" + h : h;
      }
      return hex;
    }

    function hashOrNull(file) {
      return file.exists() && file.isFile() ? sha256Hex(file) : null;
    }

    function readManifest() {
      var manifestFile = child(storeDir, "manifest.sha256");
      if (!manifestFile.exists()) {
        return null;
      }
      var fis = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
      fis.init(manifestFile, 0x01, 0444, 0);
      var cis = Cc["@mozilla.org/intl/converter-input-stream;1"].createInstance(Ci.nsIConverterInputStream);
      cis.init(fis, "UTF-8", 0, Ci.nsIConverterInputStream.DEFAULT_REPLACEMENT_CHARACTER);
      var text = "";
      var str = {};
      while (cis.readString(4096, str) != 0) {
        text += str.value;
      }
      cis.close();
      var store = { css: {}, icons: {}, assetsDir: null };
      text.split("\n").forEach(function (line) {
        var parts = line.trim().split(" ");
        if (parts.length < 3 && parts[0] != "assetsDir") {
          return;
        }
        if (parts[0] == "assetsDir") {
          store.assetsDir = parts[1];
        } else if (parts[0] == "css") {
          store.css[parts[1]] = parts[2];
        } else if (parts[0] == "icon") {
          store.icons[parts[1]] = parts[2];
        }
      });
      return store.assetsDir && store.css["userChrome.css"] && store.css["userContent.css"] ? store : null;
    }

    function readMarker() {
      var markerFile = child(chromeDir, MARKER_NAME);
      if (!markerFile.exists()) {
        return null;
      }
      var fis = Cc["@mozilla.org/network/file-input-stream;1"].createInstance(Ci.nsIFileInputStream);
      fis.init(markerFile, 0x01, 0444, 0);
      var cis = Cc["@mozilla.org/intl/converter-input-stream;1"].createInstance(Ci.nsIConverterInputStream);
      cis.init(fis, "UTF-8", 0, Ci.nsIConverterInputStream.DEFAULT_REPLACEMENT_CHARACTER);
      var text = "";
      var str = {};
      while (cis.readString(65536, str) != 0) {
        text += str.value;
      }
      cis.close();
      try {
        var marker = JSON.parse(text);
        if (marker.protocol != PROTOCOL || !marker.css) {
          return { corrupt: true };
        }
        return marker;
      } catch (e) {
        return { corrupt: true };
      }
    }

    function writeMarker(marker) {
      // Content write is not atomic, so it lands on a staging name and is
      // committed by an atomic same-directory rename.
      var staging = child(chromeDir, STAGING_PREFIX + MARKER_NAME);
      if (staging.exists()) {
        staging.remove(false);
      }
      var fos = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
      fos.init(staging, 0x02 | 0x08 | 0x20, 0644, 0);
      var cos = Cc["@mozilla.org/intl/converter-output-stream;1"].createInstance(Ci.nsIConverterOutputStream);
      cos.init(fos, "UTF-8", 0, 0);
      cos.writeString(JSON.stringify(marker));
      cos.close();
      staging.moveTo(chromeDir, MARKER_NAME);
    }

    // The only atomic step available here: renaming a regular file within one
    // directory replaces the destination. copyTo never overwrites, so content
    // always lands on a staging name first.
    function installFile(storeFile, name) {
      var staging = child(chromeDir, STAGING_PREFIX + name);
      if (staging.exists()) {
        staging.remove(false);
      }
      storeFile.copyTo(chromeDir, STAGING_PREFIX + name);
      // Store files are read-only; the profile copy must stay editable, or the
      // user cannot fix or customize anything we own.
      staging.permissions = 0o644;
      staging.moveTo(chromeDir, name);
    }

    function installAssets(assetsDirName) {
      var target = child(chromeDir, assetsDirName);
      if (target.exists()) {
        return;
      }
      var sourceDir = child(storeDir, assetsDirName);
      if (!sourceDir.exists()) {
        return;
      }
      // A directory rename onto a non-empty directory fails, so the asset set is
      // assembled under a staging name and committed by renaming it into an
      // absent name: one atomic step for the whole set.
      var staging = child(chromeDir, STAGING_PREFIX + assetsDirName);
      if (staging.exists()) {
        staging.remove(true);
      }
      sourceDir.copyTo(chromeDir, STAGING_PREFIX + assetsDirName);
      staging.permissions = 0o755;
      staging.moveTo(chromeDir, assetsDirName);
    }

    function removeIfOurs(file) {
      // Removal is a closed list: staging names, asset directories we created and
      // no longer reference, and our own legacy marker. Backup directories are
      // never removed: they are the operator's last copy of replaced bytes.
      if (!file.exists()) {
        return;
      }
      try {
        file.remove(true);
      } catch (e) {
        Cu.reportError("textfox: could not remove " + file.leafName);
      }
    }

    function sweepOwnLeftovers(currentAssetsDir) {
      // Whatever a crash can strand under our own names is swept on every run,
      // instead of only on the run that stranded it. Cleanup is thus idempotent:
      // it converges even when the interrupted run never got to do its own.
      //   - an asset set becomes unreferenced the moment the marker stops naming it
      //   - a staging name only ever exists between our copy and our rename
      // One Firefox at a time is assumed, as the profile lock already requires.
      entries(chromeDir).forEach(function (file) {
        var name = file.leafName;
        if (name === currentAssetsDir) {
          return;
        }
        if (file.isDirectory() && ASSETS_DIR_RE.test(name)) {
          removeIfOurs(file);
        } else if (name.indexOf(STAGING_PREFIX) === 0) {
          removeIfOurs(file);
        }
      });
    }

    function isLegacyMarker(file) {
      // The pre-marker protocol wrote an empty file named after the store path
      // it installed from, e.g. <32hex>-textfox-chrome.
      return /^([0-9a-f]{32})-[\w-]+$/.test(file.leafName) && file.isFile();
    }

    function refuse(reason, detail) {
      Cu.reportError(
        "textfox: refusing to install " + storeDir.leafName + " (" + reason + "). " +
        "The profile chrome directory was left completely untouched and Firefox was not restarted. " +
        detail
      );
      // Unwinds the install flow. Without this, a refusal falls through to the
      // next gate and either reports twice or writes after claiming it wrote
      // nothing.
      throw { textfoxRefused: true };
    }

    function backupOwned(names) {
      var name = BACKUP_PREFIX + Math.floor(Date.now() / 1000);
      var backup = child(chromeDir, name);
      // Two runs inside one second must not collide: this directory holds the only
      // copy of the bytes that are about to be replaced.
      for (var suffix = 2; backup.exists(); suffix++) {
        backup = child(chromeDir, name + "-" + suffix);
      }
      backup.create(Ci.nsIFile.DIRECTORY_TYPE, 0o755);
      names.forEach(function (name) {
        var file = child(chromeDir, name);
        if (file.exists()) {
          file.copyTo(backup, name);
        }
      });
      return backup;
    }

    // Returns the marker that describes the adopted install, or null when the
    // profile is not ours to take over. The result is returned rather than assigned
    // to a variable of the install flow: a nested function assigning a name the
    // caller also declares silently writes a global instead.
    function adoptLegacy(legacyFile, profile) {
      // The legacy marker names the store path the files came from, which confirms
      // ownership only while that path survives.
      var legacyStore = new FileUtils.File("/nix/store/" + legacyFile.leafName);
      var hashes = CSS_NAMES.map(function (name) {
        return hashOrNull(child(legacyStore, name));
      });
      var confirmed = legacyStore.exists() && hashes.every(function (hash, i) {
        return hash !== null && hash === profile[CSS_NAMES[i]];
      });
      if (!confirmed && !forceAdopt) {
        return null;
      }
      if (!confirmed) {
        var copied = backupOwned(CSS_NAMES);
        Cu.reportError("textfox: textfox.force-adopt is on; previous " + CSS_NAMES.join(", ") +
                       " copied to chrome/" + copied.leafName + " before taking over this legacy install.");
      }
      var adoptedMarker = {
        protocol: PROTOCOL,
        store: legacyFile.leafName,
        assetsDir: null,
        css: {},
      };
      CSS_NAMES.forEach(function (name) {
        adoptedMarker.css[name] = profile[name];
      });
      return adoptedMarker;
    }

    function installGeneration(store) {
      var marker = readMarker();
      var legacyMarker = null;
      var adopted = false;

      if (marker && marker.corrupt) {
        refuse("marker is unreadable or from an unknown protocol",
               "Only delete " + MARKER_NAME + " if you created it yourself.");
      }

      var fresh = !chromeDir.exists();
      if (fresh) {
        chromeDir.create(Ci.nsIFile.DIRECTORY_TYPE, FileUtils.PERMS_DIRECTORY);
        marker = null;
      }

      var profile = {};
      CSS_NAMES.forEach(function (name) {
        profile[name] = hashOrNull(child(chromeDir, name));
      });

      if (!marker) {
        // No current marker. That is a fresh profile, a crash before the first
        // marker commit, an adoptable pre-marker install, or somebody else's
        // directory. Only the last one is refused.
        var oursOrAbsent = CSS_NAMES.every(function (name) {
          return profile[name] === null || profile[name] === store.css[name];
        });
        if (!fresh && !oursOrAbsent) {
          legacyMarker = entries(chromeDir).filter(isLegacyMarker)[0] || null;
          if (legacyMarker) {
            marker = adoptLegacy(legacyMarker, profile);
            adopted = marker !== null;
            if (!adopted) {
              legacyMarker = null;
            }
          }
          if (!adopted && !forceAdopt) {
            refuse("no textfox marker, and the existing files are not this build's bytes",
                   "This chrome directory was not created by the NixOS textfox wrapper. Move it aside, or set textfox.force-adopt to true to have textfox back the files up and take over.");
          }
        }
        if (!marker) {
          // Nothing left to disprove: take the directory as ours, and every absent
          // or differing file is simply stale. adoptLegacy() has already set a
          // marker when it took the directory over.
          marker = { protocol: PROTOCOL, store: null, assetsDir: null, css: {} };
        }
      }

      // Per-file gate. Bytes matching the marker mean "ours, outdated"; bytes
      // matching the store mean "already current", which is also the state after a
      // crash between our renames. Anything else is not ours to replace.
      var mismatched = CSS_NAMES.filter(function (name) {
        return profile[name] !== null && profile[name] !== marker.css[name] &&
               profile[name] !== store.css[name];
      });
      if (mismatched.length) {
        if (!forceAdopt) {
          refuse(mismatched.join(" and ") + " matches neither what textfox installed nor this build",
                 "Those files were changed after installation. Restore them, move them aside, or set textfox.force-adopt to true to have textfox back them up and take over.");
        }
        var copiedAside = backupOwned(mismatched);
        Cu.reportError("textfox: textfox.force-adopt is on; previous " + mismatched.join(", ") +
                       " copied to chrome/" + copiedAside.leafName);
      }

      var stale = CSS_NAMES.filter(function (name) {
        return profile[name] !== store.css[name];
      });

      // The stylesheet references the asset directory, so assets land first.
      installAssets(store.assetsDir);
      stale.forEach(function (name) {
        installFile(child(storeDir, name), name);
      });

      var commit = stale.length || marker.store !== storeDir.leafName ||
            marker.assetsDir !== store.assetsDir || adopted;
      if (commit) {
        // The marker rename is the commit point: until it happens, the profile still
        // describes itself as the previous generation, and the next start repeats
        // the same convergent work.
        writeMarker({
          protocol: PROTOCOL,
          store: storeDir.leafName,
          assetsDir: store.assetsDir,
          css: store.css,
        });
      }

      // Cleanup sits outside the commit on purpose: it is idempotent, and a crash
      // here must not be able to strand anything, because the next start repeats it
      // even when nothing else is stale.
      if (legacyMarker) {
        removeIfOurs(legacyMarker);
      }
      sweepOwnLeftovers(store.assetsDir);

      if (stale.length) {
        var appStartup = Cc["@mozilla.org/toolkit/app-startup;1"].getService(Ci.nsIAppStartup);
        appStartup.quit(Ci.nsIAppStartup.eForceQuit | Ci.nsIAppStartup.eRestart);
      }
    }

    try {
      var store = readManifest();
      if (!store) {
        refuse("generated manifest is missing or incomplete",
               "Rebuild the textfox package; this store path is not usable.");
      }
      installGeneration(store);
    } catch (error) {
      // refuse() unwinds through here. Anything else is a bug, and a half-installed
      // generation that reports nothing is worse than a noisy one.
      if (!error || error.textfoxRefused !== true) {
        Cu.reportError("textfox: installing " + storeDir.leafName + " failed: " + error);
      }
    }

    /* END TEXTFOX AUTOCONFIG */
  '';

in
wrapFirefox browser (
  lib.removeAttrs args [
    "configCss"
    "extraUserChrome"
    "extraUserContent"
  ]
  // {
    pname = args.pname or "textfox";
    extraPrefs = configScript + (args.extraPrefs or "");
  }
)
