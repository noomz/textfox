"""Firefox theme-picker themes for painted checks (V15): fetch, install and enable one.

Firefox 159.0 `moz-src/browser/themes/ThemesList.sys.mjs` offers the built-in Light,
Dark and Alpenglow themes and 11 Nova themes the picker installs from AMO. The Nova
files are pinned here by AMO's sha256 and cached under ~/.cache/textfox-firefox-live/themes,
so a run checks the same theme every time (the download is a network request).
The image themes (IMAGES, V19) are AMO-hosted the same way: a theme's own art, not the picker's.

A theme spec is `ID` or `ID:light` / `ID:dark`. The scheme sets `ui.systemUsesDarkTheme`,
which picks a Nova theme's `theme` (light) or `dark_theme` variant and the built-in
themes' own scheme handling; without one the OS scheme decides.
"""
import hashlib
import os
from pathlib import Path
import time
import urllib.request

AMO = 'https://addons.mozilla.org/firefox/downloads/file'
# id: (AMO file path, sha256), version 1.1 each
NOVA = {
    'nova-sun@mozilla.org': ('5048373/nova_sun-1.1.xpi', '2ba8f6db19ae5215d0738ec0f20d274d7ebd32d531d0d437c843005dd0f61c0a'),
    'nova-spark@mozilla.org': ('5048374/nova_spark-1.1.xpi', '6513ee1b3aaabb30db38acd9be7bd348f7cacabee12aeb48c0c2fd977193ff96'),
    'nova-flame@mozilla.org': ('5048384/nova_flame-1.1.xpi', '3e0280637d76785aad707d4a9b14b649b307e0f684c88bd62c2e31288fee4616'),
    'nova-flare@mozilla.org': ('5048381/nova_flare-1.1.xpi', 'fb9cd2040d98e4272f06c218f8fe0c41a14d1e11fc4803d61790e1ea3d5635e8'),
    'nova-lavender@mozilla.org': ('5048377/nova_lavender-1.1.xpi', '023e1ebf9f2bc4e571e7f9bf124583bc772b1db0ff4932f326cd9154c1aceff6'),
    'nova-dusk@mozilla.org': ('5048387/nova_dusk-1.1.xpi', 'c1546c10762e3e22442e46c5a8adc1e88a0035796491dfb52a8f4847ce54d9e9'),
    'nova-lagoon@mozilla.org': ('5048378/nova_lagoon-1.1.xpi', '4395521f4d163b0543ba17296cc50d8065f641378fe14c755950379938b65490'),
    'nova-pine@mozilla.org': ('5048376/nova_pine-1.1.xpi', 'dc6bbedeeccb29cc9460335af4e37013619ca0e44a8322d5b7cc1619e5062c67'),
    'nova-tide@mozilla.org': ('5048371/nova_tide-1.1.xpi', '1781ca2ed834153f17f92cb2edbf2025bb608201774c3c948b20a4c5cc6facf3'),
    'nova-ash@mozilla.org': ('5048390/nova_ash-1.1.xpi', '069e42eb7d1f628707f7bdbe05db381f832ac0125f00d5a99e2673b3f7c037c2'),
    'nova-smoke@mozilla.org': ('5048375/nova_smoke-1.1.xpi', '3e1f6ba81715538e09be38f37a206e538768189b5bf1aa41c1e40f89e7d33665'),
}
# id: (AMO file path, sha256). Art a theme ships, for --tf-theme-images (V19, issue #1).
IMAGES = {
    # additional_backgrounds images/0.png: APNG 540x115, tiled repeat, right top; frame rgb(12,11,21).
    '{5adf2485-4acd-42a8-b04c-1b0a6b03ddd0}': (
        '4061627/purple_starfield_animated-1.0.xpi', '75fd47637c2b33fc333b801a5322eccd11cd5fc97acc770a6eff5cdc4d7b6c73'),
    # theme_frame: an 8k JPEG landscape (header image); frame and toolbar rgba(204,112,133,1).
    '{cefe0497-fb8e-4407-a806-19acd3ed20fe}': (
        '4660468/rosy_sky-4.0.xpi', '1e3112d822da2e89357ebf07a369e0a84d92f292614e2f6b57c29b38b86da6e1'),
    # additional_backgrounds images/0.png: static PNG 480x270, tiled repeat, right top; frame black.
    '{2140e74f-2373-4cd1-85dd-984632a6abc7}': (
        '5117135/aiden_made_a_topo_theme-1.0.xpi', 'd2695a1ecaf6111179e6b81ae89586e4dc86baacc1ecf1b0f02b256a502a995d'),
}
STARFIELD = '{5adf2485-4acd-42a8-b04c-1b0a6b03ddd0}'
AMO_HOSTED = {**NOVA, **IMAGES}
BUILT_IN = ('firefox-compact-light@mozilla.org', 'firefox-compact-dark@mozilla.org', 'firefox-alpenglow@mozilla.org')
# BuiltInThemeConfig `inApp`: Firefox's own CSS forced to one color scheme, not an lwtheme
# (no `lwtheme` attribute, no --lwt-* colors), so textfox's -moz-dialog default applies.
IN_APP = {'firefox-compact-light@mozilla.org': 'light', 'firefox-compact-dark@mozilla.org': 'dark'}
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'textfox-firefox-live' / 'themes'
# One light and one dark setup of each kind: a Nova theme's two variants and classic Light and Dark.
DEFAULT_THEMES = ['firefox-compact-dark@mozilla.org:dark', 'firefox-compact-light@mozilla.org:light',
                  'nova-ash@mozilla.org:dark', 'nova-ash@mozilla.org:light']
EVERY_THEME = ([f'{t}:{s}' for t in NOVA for s in ('light', 'dark')] +
               ['firefox-compact-dark@mozilla.org:dark', 'firefox-compact-light@mozilla.org:light',
                'firefox-alpenglow@mozilla.org:light', 'firefox-alpenglow@mozilla.org:dark'])
# Setups with art (V19): the three image themes, and a Nova theme's two variants (gradients).
# An image theme has no light/dark variant, so each runs with the OS scheme light and dark (V20):
# Firefox resets some content elements' color-scheme, which then follow the OS, not the theme (B25).
ART_THEMES = [*(f'{t}:{s}' for t in IMAGES for s in ('light', 'dark')),
              'nova-ash@mozilla.org:dark', 'nova-ash@mozilla.org:light']

# `--art` on the painted harnesses (V19): config.css lines and prefs per mode. `images` turns
# --tf-theme-images on; `gradient` turns --tf-theme-gradient on and, because Linux's native
# theme paints browser Nova's gradient transparent, stands in a visible 50%-transparent
# violet-to-orange gradient shaped like macOS's (tokens-platform.css); probe-only. Animated
# art is held still (hold_still) so captures compare; test-theme-art-live.py checks the
# animation itself.
TEST_GRADIENT = 'linear-gradient(96deg, rgb(130 80 200 / .5) 39.84%, rgb(230 120 40 / .5) 101.72%)'
ART_CONFIG = {
    'none': '',
    'images': ':root { --tf-theme-images: 1; }\n',
    'gradient': f':root {{ --tf-theme-gradient: 1; --toolbox-background-gradient: {TEST_GRADIENT} !important; }}\n',
}


HOLD_STILL_JS = r"""
const utils = window.windowUtils, done = arguments[arguments.length - 1];
utils.imageAnimationMode = Ci.imgIContainer.kDontAnimMode;
// Repaint all art once: a part repainted later would show another frame than the rest.
const off = 'data:text/css,' + encodeURIComponent(
  ':root { --tf-theme-images: 0 !important; --tf-theme-gradient: 0 !important; }');
utils.loadSheetUsingURIString(off, utils.AUTHOR_SHEET);
requestAnimationFrame(() => requestAnimationFrame(() => {
  utils.removeSheetUsingURIString(off, utils.AUTHOR_SHEET);
  requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
}));
"""


def hold_still(m, art):
    """Stop animated images in the current chrome window for an `--art` run. The pref
    image.animation_mode does not reach chrome windows; the window's own mode does."""
    if art != 'none':
        m.execute_async_script(HOLD_STILL_JS, sandbox='system')
        time.sleep(1)


def suite_runs(themes, combos, configs, control='known-bad'):
    """A painted suite's runs (theme, *combo, config), per V23: every good config on every combo
    (Nova state, layout), the control once per theme on a combo rotating across themes, and with
    no theme on every combo."""
    runs = [(theme, *combo, kind)
            for i, theme in enumerate(themes or [None])
            for j, combo in enumerate(combos)
            for kind in configs
            if kind != control or theme is None or j == i % len(combos)]
    unchecked = set(themes or [None]) - {run[0] for run in runs if run[-1] == control}
    if control in configs and unchecked:
        raise RuntimeError(f'V23: no {control} run for {sorted(map(str, unchecked))}')
    return runs


def parse(spec):
    """'ID[:light|dark]' -> (id, scheme or None); unknown themes fail."""
    theme, _, scheme = spec.partition(':')
    if theme not in AMO_HOSTED and theme not in BUILT_IN:
        raise ValueError(f'unknown theme {theme}; known: {", ".join([*AMO_HOSTED, *BUILT_IN])}')
    if scheme not in ('', 'light', 'dark'):
        raise ValueError(f'theme scheme must be light or dark, not {scheme}')
    return theme, scheme or None


def xpi(theme):
    """Path of the pinned AMO file for a Nova or image theme, downloaded once and checked by sha256."""
    path_part, sha = AMO_HOSTED[theme]
    path = CACHE / f'{theme}-{sha[:12]}.xpi'
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f'{AMO}/{path_part}', timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError(f'{theme}: AMO file does not match pinned sha256 {sha}')
        path.write_bytes(data)
    return path


def prefs(spec):
    """user.js prefs a theme spec needs before startup (the color scheme)."""
    _, scheme = parse(spec)
    return {'ui.systemUsesDarkTheme': int(scheme == 'dark')} if scheme else {}


APPLY_JS = r"""
const [id, file] = arguments;
const done = arguments[arguments.length - 1];
const {AddonManager} = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
(async () => {
  let addon = await AddonManager.getAddonByID(id);
  if (!addon && file) {
    const install = await AddonManager.getInstallForFile(new FileUtils.File(file));
    addon = await install.install();
  }
  if (!addon) throw new Error(`theme ${id} not installed`);
  await addon.enable();
  const root = document.documentElement;
  for (let i = 0; i < 100 && !arguments[2] && !root.hasAttribute('lwtheme'); i++) await new Promise(r => setTimeout(r, 50));
  await new Promise(r => setTimeout(r, 1000));  // let the theme's colors and images paint
  const style = getComputedStyle(root);
  return {id: addon.id, version: addon.version, active: addon.isActive, lwtheme: root.hasAttribute('lwtheme'),
          dark: matchMedia('(prefers-color-scheme: dark)').matches,
          accent: style.getPropertyValue('--lwt-accent-color'),
          toolbar: style.getPropertyValue('--toolbar-background-color')};
})().then(done, e => done({error: String(e)}));
"""


def apply(m, spec):
    """Install (AMO-hosted: the pinned file) and enable a theme in the chrome context; return its state."""
    theme, scheme = parse(spec)
    file = str(xpi(theme)) if theme in AMO_HOSTED else None
    state = m.execute_async_script(
        "const {FileUtils} = ChromeUtils.importESModule('resource://gre/modules/FileUtils.sys.mjs');" + APPLY_JS,
        script_args=[theme, file, theme in IN_APP], sandbox='system', script_timeout=60000)
    problems = []
    if 'error' in state:
        problems.append(state['error'])
    elif theme in IN_APP:
        if not state['active'] or state['lwtheme'] or state['dark'] != (IN_APP[theme] == 'dark'):
            problems.append(f'in-app theme not in effect as {IN_APP[theme]}')
    elif not (state['active'] and state['lwtheme'] and state['accent']):
        problems.append('theme not in effect')
    # An image theme's colors set chrome's scheme; its spec's scheme is the OS's (V20).
    if scheme and not problems and theme in IMAGES:
        state['os_dark'] = m.execute_script("return Services.prefs.getIntPref('ui.systemUsesDarkTheme', -1)") == 1
        if state['os_dark'] != (scheme == 'dark'):
            problems.append(f'OS color scheme not {scheme}')
    elif scheme and not problems and state['dark'] != (scheme == 'dark'):
        problems.append(f'color scheme not {scheme}')
    if problems:
        raise RuntimeError(f'theme {spec}: {problems}: {state}')
    state['spec'] = spec
    return state


ART_JS = r"""
const root = document.documentElement;
return {image: getComputedStyle(root).getPropertyValue('--toolbox-background-image').trim(),
        lwtheme: root.hasAttribute('lwtheme')};
"""


def art_expected(m, art):
    """Whether Firefox itself has the art an `--art` mode keeps (V19), from Firefox's own
    `--toolbox-background-image` at :root, never from textfox's CSS: a theme's layers for
    images, browser Nova's gradient (no theme) for gradient."""
    state = m.execute_script(ART_JS, sandbox='system')
    # Linux's native browser Nova gradient is image(transparent): nothing to show.
    has = state['image'] not in ('', 'none', 'image(transparent)')
    state['mode'] = art
    state['expected'] = has and art != 'none' and state['lwtheme'] == (art == 'images')
    return state
