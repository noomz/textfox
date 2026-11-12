# Firefox Nova port

## §G

Port textfox to Firefox Nova without losing TUI appearance, existing configuration, accessibility, or browser functionality.

## §C

- Keep Nova disabled and retain README warning until support checks pass.
- Preserve existing uncommitted work and `.pi/`. Keep `NOVA-PORT.md` as investigation record.
- Target Firefox release and build per delivery path; record effective preferences and defaults. Firefox 159.0 build `20261022150525` is the investigated baseline, not proof for every path.
- Test Nova on and off while Firefox supports both. Test browser and new-tab Nova preferences independently.
- Never hard-code theme colors. Preserve `--tf-*` overrides and Firefox theme variables.
- Disposable new-tab probes may make network requests. Automation feed and widget overrides are probe-only.
- Do not delete profile files to unblock NixOS updates. Preserve old store generation and back up profile before any manual recovery; no recovery procedure is verified.
- Rootless nix-portable with proot cannot prove NixOS VM or activation behavior requiring mount namespaces or a real Nix daemon.
- `packages.default` builds locally, and home-manager activation is proven only in a disposable VM against a fetched home-manager source from the branch that pairs with the nixpkgs source (unstable `master`, not a release branch). Standalone home-manager hosts and other source pairings remain unverified.
- `Cu.reportError` output does not reach a Firefox process log, so a headless run can only assert what a refusal leaves behind, not what it says.
- `NOVA-PORT.md` contains observations, limitations, and resume commands. Temporary `/tmp` artifacts may disappear.

## §I

- I.manual: `tf-install.sh`, `tf-uninstall.sh`, `chrome/`, optional `user.js`, and user-owned `chrome/config.css`.
- I.home: `nix/modules/home-manager.nix`, Firefox profiles, generated `config.css`, and `extraConfig`.
- I.nixos: `nix/modules/nixos.nix`, `nix/pkgs/wrapTextfox.nix`, generated CSS, autoconfig, store paths, and profile restart behavior.
- I.config: `chrome/defaults.css`, `nix/modules/options.nix`, generated `configCss`, and existing `--tf-*` options.
- I.prefs: `browser.nova.enabled` and `browser.newtabpage.activity-stream.nova.enabled`, including persisted profile preferences.
- I.chrome: tabs, sidebar, toolbar, URL bar, menus, panels, findbar, split view, private windows, new-tab, and supported `about:` pages.
- I.probes: `tools/firefox-chrome/firefox-live.sh`, `ff_inspect.py`, `ff_newtab.py`, and live regression scripts.

## §V

- V1. Nova support remains unclaimed and existing disabling preference remains until UI, delivery, migration, and accessibility checks pass.
- V2. Manual, home-manager, and NixOS delivery preserve user configuration, unrelated profile files, icons, and CSS precedence. Failed or interrupted updates remain retryable without profile data loss.
- V3. Existing profiles and fresh profiles reach the intended preference state through a verified migration. Removing a preference assignment alone is not proof of migration.
- V4. Existing `--tf-*` customization continues to affect computed styles. New or renamed variables have matching defaults, Nix options, and generated `configCss` entries.
- V5. Nova and supported classic UI retain TUI styling, readable layout, visible focus, keyboard access, contrast, forced colors, reduced motion, and RTL behavior.
- V6. Browser and new-tab preference combinations are verified separately against effective values, initialized content, and real Firefox behavior. Computed styles alone do not prove visual or interaction correctness.
- V7. NixOS update safety and activation are verified against generated autoconfig and a real supported environment. Mock file APIs, Nix evaluation, and proot alone do not prove activation.
- V8. Layout tests distinguish measured behavior from estimates and cannot pass on inconclusive pseudo-element geometry or clipped content.
- V9. Regression checks cover supported delivery paths and relevant configuration options before support claims change.

## §T

| id | status | task | cites |
|---|---|---|---|
| T1 | x | Pin Firefox release and build per delivery path. Record effective Nova preferences and defaults; establish repeatable vanilla and textfox baselines. | V1,V6,I.prefs,I.probes |
| T2 | x | Inventory selectors and Firefox-owned variables against release source and live DOM for all listed components. Record verified gaps. | V5,V6,I.chrome |
| T3 | x | Build reliable failing layout checks. Trace Nova logo grid and shortcut width constraints; fix verified defects without replacing configured font. Verify responsive, RTL, clipping, and focus behavior. | V4,V5,V6,V8,I.config,I.chrome,I.probes |
| T4 | x | Port remaining verified component gaps. Test interactions, menu activation, accessibility, and Nova preference matrix in real Firefox. | V4,V5,V6,I.config,I.chrome,I.prefs |
| T5 | x | Compare manual imports and NixOS concatenation order. Design and verify safe ownership, migration, failure, and retry protocol; test manual, home-manager, and real NixOS delivery separately. | V2,V7,V9,I.manual,I.home,I.nixos; proofs: `test-nixos-update.js` (19 scenarios), `test-nixos-update-vm.sh` (real NixOS), `test-home-manager-vm.sh` (real home-manager activation) |
| T6 | x | Test existing configuration options against computed styles across delivery paths. Sync defaults, Nix options, and generated CSS for any changed variables. | V2,V4,V9,I.config,I.manual,I.home,I.nixos; proofs: `check-option-wiring.py` (24 options), `check-option-parity.py`, `test-vertical-tabs-option-live.py` (6 cases), `check-home-manager-options.py`, `test-home-manager-vm.sh`, `test-nixos-generated-live.py` |
| T7 | x | Design and verify fresh-profile and existing-profile preference migration across delivery paths. Keep Nova disabled until other support gates pass. | V1,V3,V6,I.prefs,I.manual,I.home,I.nixos; proofs: `check-pref-parity.py` + `test-pref-parity.py` (6), `test-manual-install-files.py` (9), `test-manual-install-live.py` (fresh + existing profile), `test-nixos-prefs-live.py`, `test-home-manager-vm.sh` (seeded profile), `test-nixos-prefs-vm.sh` (seeded profile) |
| T8 | x | Run full live regression, accessibility and platform matrix, relevant Nix checks, `git diff --check`, and independent review. Update support and migration guidance only after gates pass. | V1,V2,V3,V4,V5,V6,V7,V8,V9,I.manual,I.home,I.nixos,I.chrome,I.probes; verdict: Nova support not claimed; proofs: full static/live/Nix/VM sweep at `ebd471f`, `test-high-contrast-live.py` (8), `test-rtl-locale-live.py` (8), independent review (B6–B8 fixed) |
| T9 | x | Make interrupted NixOS installs retryable without `textfox.force-adopt`: fix autoconfig findings F1/F2 (same- and cross-generation retry after a kill before the marker commit), F3 (partly removed asset directory reused) and F4 (stranded legacy marker). | V2,V7,V9,I.nixos; proofs: `check-nixos-update-exhaustive.js` (findings no longer reproduce, no new violations), `test-nixos-update-vm.sh` |
| T10 | . | Manual uninstall offers the user's pre-textfox backup instead of the newest one (previous textfox files). NixOS repairs a damaged file inside the current `textfox-assets-<hash>` directory. | V2,V9,I.manual,I.nixos |
| T11 | . | Keep text readable for arbitrary `--tf-*` colors, including the app-menu foreground pairing (`--panel-text-color` against a configured background). | V4,V5,I.config,I.chrome |
| T12 | . | Run the Windows and macOS matrix: Windows high contrast (`browser.display.document_color_use` 0), macOS flat context menu, manual scripts under macOS `sh`. Needs a Windows image and a Mac host. | V5,V6,V9,I.manual,I.chrome |

## §B

| id | date | cause | fix |
|---|---|---|---|
| B1 | 2026-11-05 | NixOS autoconfig imports removed `FileUtils.jsm` on Firefox 159. | V2,V7; fixed: import moved to `FileUtils.sys.mjs`; live NixOS VM installs on Firefox 159.0.1 (`test-nixos-update-vm.sh`, phases A–C). |
| B2 | 2026-11-09 | Firefox 159 legacy `-moz-bool-pref` queries did not match in sampled profiles, disabling conditional theme rules. | V4,V5,V6; fixed: all four gates converted to `@media -moz-pref(...)`; regression `test-pref-icons-live.py` (8 cases, both Nova states). |
| B3 | 2026-11-12 | Manual `tf-install.sh` moved the whole `chrome` directory into a backup on reinstall, taking the user's `config.css` out of the live profile, and replaced an existing `user.js` without keeping the old bytes. | V2,V9; fixed: only textfox's own files are moved aside, `chrome/` is merged instead of copied as a directory, an existing `user.js` is copied to `user.js-<stamp>.bak`; regression `test-manual-install-files.py` (3 cases), live `test-manual-install-live.py` still passes. |
| B4 | 2026-11-12 | NixOS autoconfig: pre-marker adoption assigned names the install flow also declares, so adoption never took effect; `refuse()` did not stop the flow, so a foreign profile was refused twice and a force-adopted one reported a refusal it did not perform; cleanup ran only on the committing run, so a crash between the marker commit and cleanup leaked the old asset set; two backups in one second collided. | V2,V7,V9; fixed: adoption returns its marker, `refuse()` unwinds, leftovers under textfox's own names are swept on every start, backups take a suffix; regression `test-nixos-update.js` (19 scenarios, including a crash before every mutating step) plus `test-nixos-update-vm.sh` (5 phases). |
| B5 | 2026-11-12 | Manual `tf-install.sh` replaced an existing `user.js` with textfox's file. B3 kept the old bytes as a backup, but the user's own preferences stopped applying, and reinstalling or uninstalling could not tell textfox's lines from the user's. | V2,V3,V9; fixed: textfox preferences are merged into `user.js` inside a marker block (stale block replaced, unclosed marker refused, backup kept), uninstall removes only that block or a file that is only textfox's; regression `test-manual-install-files.py` (9 cases), live `test-manual-install-live.py` (existing profile with its own `user.js`). |
| B6 | 2026-11-12 | Manual `tf-uninstall.sh` ran `rm -rf` on the profile's whole `chrome/` directory, deleting the user's `config.css` and any other files the user kept there; restoring a backup then moved it onto `chrome/` instead of beside the kept files. | V2,V9; fixed: uninstall removes only the files the checkout's `chrome/` ships, removes directories only once empty, and restores backup entries beside the user's files without overwriting them; regression `test-manual-install-files.py` (12 cases, 3 new uninstall cases red before the fix). |
| B7 | 2026-11-12 | T6 wired `--tf-display-vertical-tabs` as `display: … !important` on `box#vertical-tabs`, overriding Firefox's own `display: none` for a strip without `[visible]`; with `sidebar.revamp` on and horizontal tabs, an empty bordered strip showed in the sidebar. `test-vertical-tabs-option-live.py` tested only vertical tabs on. | V4,V5,V9; fixed: the display override applies only to `box#vertical-tabs[visible]`; regression `test-vertical-tabs-option-live.py` (10 cases, 4 new horizontal-tab cases red before the fix). Found by T8 independent review. |
| B8 | 2026-11-12 | Manual scripts: the B6 uninstall used process substitution, which macOS `sh` (bash 3.2 in POSIX mode) rejects at parse time; it also took its file list from the checkout's `chrome/`, so a `config.css` kept there deleted the profile's; and both scripts accepted a `user.js` whose end marker preceded its begin marker, so stripping dropped every user line after the begin marker. | V2,V9; fixed: pipe loops, `config.css` never treated as shipped, reversed markers refused by install and uninstall; regression `test-manual-install-files.py` (14 cases, 2 new cases red before the fix). Found by T8 independent review. |
