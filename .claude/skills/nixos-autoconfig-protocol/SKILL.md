---
name: nixos-autoconfig-protocol
description: >-
  Exhaustive bounded check of the NixOS textfox install protocol: the autoconfig
  script in nix/pkgs/wrapTextfox.nix that copies CSS, icons, and the
  textfox-installed.json marker into a Firefox profile on every start. Use
  before and after any change to that script (configScript, installGeneration,
  adoptLegacy, installAssets, installFile, writeMarker, sweepOwnLeftovers,
  backupOwned, refuse, readManifest), to the textfoxChrome builder or manifest
  format, or to the shim in tools/firefox-chrome/test-nixos-update.js. Also use
  when asked whether a crash, rollback, user edit, or force-adopt can lose user
  files, strand files, or wedge the install. Triggers: "autoconfig", "install
  protocol", "marker", "force-adopt", "legacy adoption", "crash mid-install",
  "V2", "V7", "B4".
---

# NixOS autoconfig install protocol: exhaustive check

## What it proves

The NixOS delivery path (`nix/modules/nixos.nix` → `nix/pkgs/wrapTextfox.nix`) runs a JavaScript autoconfig on every Firefox start. It owns a few names in `<profile>/chrome/` and must never harm the rest. A break means lost user CSS, broken icons, or a Firefox that refuses textfox until someone edits `about:config`.

`tools/firefox-chrome/check-nixos-update-exhaustive.js` runs that script **unmodified** (extracted from `wrapTextfox.nix` between `/* TEXTFOX GENERATED CONFIG */` and `/* END TEXTFOX AUTOCONFIG */`) through the XPCOM shim exported by `tools/firefox-chrome/test-nixos-update.js`, on a real temp directory. There is no hand-written model to drift from the code. It tries **every** sequence of up to `--depth` events, from four starting profiles, and deduplicates profiles by content.

Events:
- Firefox starts on generation A or B, and the script runs to the end.
- The same, but Firefox dies before mutating step k, for every k. Death is total: once the step fires, later mutating steps fail too, even inside `removeIfOurs`'s catch.
- The same, but Firefox dies part-way through a recursive directory removal (one entry gone, the directory still there).
- The user writes their own bytes into `userChrome.css` or `userContent.css` (at most once per path).
- The user deletes either file.
- The user flips `textfox.force-adopt`.

Starting profiles: fresh (no `chrome/`); hand-written `chrome/` with the user's own `userChrome.css`, `notes.txt`, `mine/extra.css`; a pre-marker legacy install whose store path still exists; the same legacy install after garbage collection.

Properties, each checked after every event:

| id | plain statement | harm if broken |
|---|---|---|
| P1 keep-user-bytes | Bytes the user wrote into an owned name stay in `chrome/`, either in place or in a `textfox-backup-*` directory, until the user removes them. | User CSS lost. |
| P2 others-untouched | `prefs.js`, `chrome/notes.txt`, and `chrome/mine/` keep their bytes. | textfox deletes files it does not own. |
| P3 refuse-only-foreign | A complete run refuses only when force-adopt is off and an owned CSS file holds bytes textfox cannot prove are its own. A refusal changes nothing and does not restart Firefox. | textfox wedges itself after its own crash, or refuses and still writes. |
| P4 converge | A complete run that does not refuse leaves this generation's CSS, a marker naming it, its full asset directory, no `.textfox-staging-*`, no other `textfox-assets-*`, and no legacy marker. Firefox restarts when the CSS changed, and no internal failure is reported. | Stale or half-installed theme, missing icons, leaked files. |
| P5 idempotent | A second complete run of the same generation makes no change and does not restart. | Restart loop, or needless writes on every start. |

"Bytes textfox can prove" means generation A or B bytes, or legacy bytes while the legacy marker and its store path both exist.

## Run it

```sh
node tools/firefox-chrome/check-nixos-update-exhaustive.js            # depth 4: ~15k profiles, ~60 s
node tools/firefox-chrome/check-nixos-update-exhaustive.js --depth 3  # ~2.2k profiles, ~8 s; use while iterating
node tools/firefox-chrome/test-nixos-update.js                        # the 21 hand-written scenarios; run too
```

Requirements: Node.js only. The repo was checked with Node 26.8.2. No Nix build is needed: the script is read from the `.nix` source. Run depth 4 before you call a change done. Depth 3 already reaches every known finding.

Exit 0 means there are no new violations. `test-nixos-update.js` exits 0 when all 21 scenarios pass, and a failing scenario there is a regression too. For the exhaustive check, exit 1 means a counterexample appeared that is not a known finding. The output prints it as numbered steps in the script's own words, for example `killed before step 5 (copy profile/chrome/.textfox-staging-userContent.css)`. Step numbers count mutating calls (`mkdir`, `copy`, `move`, `write`, `remove`, `create`) within that one run.

## Does my change touch the verified code?

Covered code, confirmed at commit **`c445159`**:

- `nix/pkgs/wrapTextfox.nix`: all of `configScript`, plus the `textfoxChrome` builder's `manifest.sha256` format and the `textfox-assets-<digest>` naming.
- `tools/firefox-chrome/test-nixos-update.js`: the shim (`Sandbox`, `makeContext`, `codeFor`, `writeStore`, fixtures). If the shim's file semantics change, the check changes with them.

Check for drift:

```sh
git diff --stat c445159 -- nix/pkgs/wrapTextfox.nix tools/firefox-chrome/test-nixos-update.js tools/firefox-chrome/check-nixos-update-exhaustive.js
```

Any change to those files means rerun the check. Then reread the events and properties above against the new code. If the script gained a new mutating operation, owned name, pref, or start-up branch, the check must model it. Move the sync commit in this file forward only after you confirm that the check still describes the code at the new commit.

Not covered: `nixos.nix` activation, the Firefox restart itself, and real `nsIFile` behaviour inside Firefox. Those belong to `tools/firefox-chrome/test-nixos-update-vm.sh`, which is V7's proof. The manual install (`tf-install.sh`) and home-manager are not covered either.

## Real bug or checker error?

For each `NEW VIOLATION`:

1. Replay the steps by hand against the code. Open `installGeneration` and follow the gates in order: marker read (and its `legacy` name) → `fresh` → `oursOrAbsent` → `adoptLegacy` → per-file `mismatched` gate (marker, store, or marker `pending` bytes) → `installAssets` (manifest-verified) → intent `writeMarker` (`pending`) → `installFile` → legacy marker removal → `writeMarker` commit → `sweepOwnLeftovers`. If the code really does that, it is a real bug.
2. It is a **checker error** if the counterexample needs something the system cannot do, for example a crash inside a single `move` (renames are atomic) or two Firefox instances on one profile (the profile lock forbids it). Fix the event or the property in `check-nixos-update-exhaustive.js`, and say why in a comment.
3. It is an **intended behaviour change** if you meant the new behaviour, for example a new refusal reason. Update the property and its row in the table above, and the commit message must say so. Never widen `KNOWN` to absorb a new failure you did not mean.

Do not weaken a property, lower the depth, or drop an event to get a pass. Report the counterexample instead.

## Known findings

These print as `known Fn` on every run and do not fail the exit code. When a fix lands, the run prints `Fn no longer reproduces`. Then delete its entry from `KNOWN` in the checker and from this list. Do this only when the run has no `NEW VIOLATION`: a new defect can hide a known one, and then the run says so instead.

F1–F4 were fixed by T9 (intent marker with `pending` before any CSS rename, legacy marker name carried in the marker, manifest-verified asset directory). Their regressions are in `test-nixos-update.js`.

- **F5 (P3): a legacy install with one CSS file deleted by the user is refused.** `adoptLegacy` requires both files. This may be intended conservatism. Confirm with the maintainer before "fixing" it.

## Proven able to fail

At `f6cd26f`, each of these one-line breaks in `wrapTextfox.nix` turned into a `NEW VIOLATION` at depth 3:

| break | caught by |
|---|---|
| `var copiedAside = backupOwned(mismatched);` → no backup | P1 |
| sweep any directory, not only `ASSETS_DIR_RE` | P2 (and P1) |
| drop `profile[name] !== marker.css[name] &&` from the per-file gate | P3 (`marker: records these bytes`) |
| stop sweeping `STAGING_PREFIX` names | P4 leftovers |
| `var commit = true \|\| …` | P5 |

To repeat one, edit the line, run depth 3, and see exit 1. Then `git checkout -- nix/pkgs/wrapTextfox.nix`.

## Scope and bounds

- Two store generations (A, B, with different icon sets) and one legacy generation. That is enough to tell "this store", "the marker's store", and "some other textfox".
- Depth 4 events by default. A defect that needs more events is not ruled out.
- At most one user edit, so the check does not cover a user who edits twice and expects the first edit kept. Under P1, overwriting your own bytes is your own doing.
- A crash is death between mutating calls, or in the middle of a recursive remove. A partial `copyTo` is not modelled. It only ever writes a `.textfox-staging-*` name, which is swept.
- One Firefox per profile, as the profile lock enforces.
- File semantics come from the shim, on Node's `fs` on Linux. Real Firefox behaviour is the VM test's job.
- Backup directory names are compared without their timestamp.
