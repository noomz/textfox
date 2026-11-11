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
| T5 | . | Compare manual imports and NixOS concatenation order. Design and verify safe ownership, migration, failure, and retry protocol; test manual, home-manager, and real NixOS delivery separately. | V2,V7,V9,I.manual,I.home,I.nixos |
| T6 | . | Test existing configuration options against computed styles across delivery paths. Sync defaults, Nix options, and generated CSS for any changed variables. | V2,V4,V9,I.config,I.manual,I.home,I.nixos |
| T7 | . | Design and verify fresh-profile and existing-profile preference migration across delivery paths. Keep Nova disabled until other support gates pass. | V1,V3,V6,I.prefs,I.manual,I.home,I.nixos |
| T8 | . | Run full live regression, accessibility and platform matrix, relevant Nix checks, `git diff --check`, and independent review. Update support and migration guidance only after gates pass. | V1,V2,V3,V4,V5,V6,V7,V8,V9,I.manual,I.home,I.nixos,I.chrome,I.probes |

## §B

| id | date | cause | fix |
|---|---|---|---|
| B1 | 2026-11-05 | NixOS autoconfig imports removed `FileUtils.jsm` on Firefox 159. | V2,V7; fix pending. |
| B2 | 2026-11-09 | Firefox 159 legacy `-moz-bool-pref` queries did not match in sampled profiles, disabling conditional theme rules. | V4,V5,V6; fix pending. |
