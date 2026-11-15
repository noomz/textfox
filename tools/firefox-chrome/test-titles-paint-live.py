#!/usr/bin/env python3
"""Painted section-title placement (V14, T27): each title paints whole on its box's top border.

textfox labels boxes with a `::before` title (navbar, tabs, main, tools, ...). V14
requires each title to paint whole and straddle its box's top border: not clipped
by the window or an ancestor, not covered by other chrome, and overlapping no
control. Computed styles cannot show any of that (B18), so this check paints.

Harness: T15's disposable headless Sway compositor (`paint_display.py`), Firefox
headed inside it, `grim` captures. For each title present:

- in place: the title's pixels are the difference between a capture and one with
  only that title hidden (`visibility: hidden` from an author sheet);
- reference: the same title moved to a fixed spot on top of everything, measured
  the same way. Its glyph box is the title's full size;
- whole: the in-place box must be at least as tall and as wide as the reference,
  less 1 device pixel. Clipping or covering shortens it;
- on the border: the in-place box must reach above the box's top border and below
  its bottom edge;
- clear of controls: the in-place box may not intersect any visible button, tab or
  toolbar button, light or shadow DOM, other than the title's own box;
- blends in (B19): the title's background is the box's own paint. Inside the box,
  just right of the title and below the top border, the dominant painted color
  must match the title's background color. A Firefox theme painting its toolbar
  color or images under the box shows the title as a patch.
  Over theme art (`--art`, V19) the box's paint is not one color, so the title
  with its text (and text shadow) made transparent must paint what the capture with it hidden
  shows, pixel for pixel within BLEND, across the title's glyph box except the
  border rows it covers; and in those border rows it must differ from that
  capture (which shows the line) in HIDES of the pixels: the title hides the line.

`--theme ID[:light|dark]` (repeatable) or `--themes default|every` runs the matrix
with that Firefox theme active (ff_themes.py, V15). `--art images|gradient` turns
the theme-art option on (ff_themes.ART_CONFIG); `--themes art` is the image themes
and nova-ash. The art check applies where Firefox itself has that art.

Matrix: browser Nova on and off, classic layout (horizontal tabs, bookmarks
sidebar) and revamp layout (vertical tabs and the tools launcher), device pixel
ratio 1 and 2 (`--dpp 1,2`), with the bookmarks toolbar and the findbar shown.
Exit 0 or 1, never inconclusive.

Run: "$HOME/.cache/textfox-firefox-live/venv/bin/python" tools/firefox-chrome/test-titles-paint-live.py
FIREFOX or PATH chooses the Firefox; `--expect-version` pins it.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import paint_display  # noqa: E402
import ff_themes  # noqa: E402

_paint = runpy.run_path(str(HERE / 'test-surface-paint-live.py'))
connect, wait, CHROME_STYLE_JS, FREEZE = (_paint[k] for k in ('connect', 'wait', 'CHROME_STYLE_JS', 'FREEZE'))
_more = runpy.run_path(str(HERE / 'test-surface-paint-more-live.py'))
stop, LAYOUTS = _more['stop'], _more['LAYOUTS']

# name: (host selector in its document scope, host lookup JS, layouts)
TITLES = {
    'navbar': ('#nav-bar', "document.getElementById('nav-bar')", ('classic', 'revamp')),
    'bookmarks': ('#PersonalToolbar', "document.getElementById('PersonalToolbar')", ('classic', 'revamp')),
    'tabs-horizontal': ('#TabsToolbar', "document.getElementById('TabsToolbar')", ('classic',)),
    'tabs-vertical': ('box#vertical-tabs', "document.getElementById('vertical-tabs')", ('revamp',)),
    'main': ('#tabbrowser-tabbox', "document.getElementById('tabbrowser-tabbox')", ('classic', 'revamp')),
    'sidebar': ('#sidebar-box', "document.getElementById('sidebar-box')", ('classic',)),
    'tools': ('.buttons-wrapper', "document.querySelector('sidebar-main')?.shadowRoot?.querySelector('.buttons-wrapper')",
              ('revamp',)),
    'find': ('findbar:not([hidden])', "gBrowser.selectedTab._findBar ?? document.querySelector('findbar:not([hidden])')",
             ('classic', 'revamp')),
}
BLEND = 4  # per channel: title background vs the box paint beside it
REF_X, REF_Y = 420, 300  # CSS px: where the reference copy of a title paints

HOSTS_JS = r"""
const HOSTS = {
  'navbar': () => document.getElementById('nav-bar'),
  'bookmarks': () => document.getElementById('PersonalToolbar'),
  'tabs-horizontal': () => document.getElementById('TabsToolbar'),
  'tabs-vertical': () => document.getElementById('vertical-tabs'),
  'main': () => document.getElementById('tabbrowser-tabbox'),
  'sidebar': () => document.getElementById('sidebar-box'),
  'tools': () => document.querySelector('sidebar-main')?.shadowRoot?.querySelector('.buttons-wrapper'),
  'find': () => document.querySelector('findbar:not([hidden])'),
};
"""

GEOMETRY_JS = r"""
const [name] = arguments;
const host = HOSTS[name]();
if (!host) return null;
const b = host.getBoundingClientRect();
const style = getComputedStyle(host);
const before = getComputedStyle(host, '::before');
if (b.width == 0 || b.height == 0 || before.display == 'none' || before.content == 'none') return null;
const controls = [];
const seen = new Set();
const walk = root => {
  for (const e of root.querySelectorAll('*')) {
    if (e.shadowRoot) walk(e.shadowRoot);
    const name = e.localName;
    if (!['toolbarbutton', 'button', 'moz-button', 'tab', 'menulist', 'checkbox'].includes(name)) continue;
    if (e === host || seen.has(e)) continue;
    seen.add(e);
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || getComputedStyle(e).visibility != 'visible') continue;
    controls.push({id: e.id || e.className || name, x: r.x, y: r.y, w: r.width, h: r.height});
  }
};
walk(document);
const bg = before.backgroundColor.match(/[\d.]+/g).slice(0, 3).map(Number);
return {x: b.x, y: b.y, w: b.width, h: b.height, borderTop: parseFloat(style.borderTopWidth),
        inShadow: host.getRootNode() != document, bg, controls};
"""

REFERENCE_JS = r"""
const [name, show] = arguments;
document.getElementById('tf-title-reference')?.remove();
if (!show) return true;
const host = HOSTS[name]();
const cs = getComputedStyle(host, '::before');
const ref = document.createElementNS('http://www.w3.org/1999/xhtml', 'div');
ref.id = 'tf-title-reference';
ref.textContent = cs.content.replace(/^"|"$/g, '');
for (const p of ['font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing',
                 'text-transform', 'padding-left', 'padding-right', 'padding-top', 'padding-bottom', 'color',
                 'background-color', 'text-shadow'])
  ref.style.setProperty(p, cs.getPropertyValue(p));
Object.assign(ref.style, {position: 'fixed', left: 'REF_Xpx', top: 'REF_Ypx', zIndex: '2147483647', margin: '0',
                          whiteSpace: 'pre'});
document.documentElement.append(ref);
return true;
""".replace('REF_X', str(REF_X)).replace('REF_Y', str(REF_Y))

SHADOW_STYLE_JS = r"""
const [css] = arguments;
const root = document.querySelector('sidebar-main')?.shadowRoot;
if (!root) return false;
let sheet = root._tfTitleSheet;
if (!sheet) { sheet = root._tfTitleSheet = new CSSStyleSheet(); root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet]; }
sheet.replaceSync(css);
return true;
"""


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Shooter:
    def __init__(self, m, display, directory):
        self.m, self.display, self.directory, self.n = m, display, Path(directory), 0

    def shoot(self, chrome_css='', shadow_css=''):
        self.m.execute_script(SHADOW_STYLE_JS, script_args=[shadow_css], sandbox='system')
        self.m.execute_async_script(CHROME_STYLE_JS, script_args=[FREEZE + chrome_css], sandbox='system')
        time.sleep(.4)
        self.n += 1
        path = self.directory / f'shot-{self.n}.ppm'
        self.display.capture(path)
        return path


def glyph_box(with_title, without, bg, region=None):
    """Box of the title's glyphs: pixels that change when the title goes and are not its background."""
    width, height, a = paint_display.read_ppm(with_title)
    _, _, b = paint_display.read_ppm(without)
    x0, y0, x1, y1 = region or (0, 0, width, height)
    xs, ys = [], []
    for y in range(max(0, y0), min(height, y1)):
        row = y * width * 3
        for x in range(max(0, x0), min(width, x1)):
            i = row + x * 3
            if max(abs(a[i + k] - b[i + k]) for k in range(3)) <= 2:
                continue
            if max(abs(a[i + k] - bg[k]) for k in range(3)) > 24:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return {'x': min(xs), 'y': min(ys), 'width': max(xs) - min(xs) + 1, 'height': max(ys) - min(ys) + 1}


def dominant(path, region):
    """Most frequent color in region (x0, y0, x1, y1) of a capture, or None if empty."""
    width, height, data = paint_display.read_ppm(path)
    x0, y0, x1, y1 = region
    tally = {}
    for y in range(max(0, y0), min(height, y1)):
        for x in range(max(0, x0), min(width, x1)):
            i = (y * width + x) * 3
            p = tuple(data[i:i + 3])
            tally[p] = tally.get(p, 0) + 1
    return max(tally, key=tally.get) if tally else None


def line_hidden(clear, hidden, here, top, bottom):
    """(pixels, rows total) of the border rows top..bottom-1 under the title's glyph box where the title
    with transparent text differs from the capture without the title, which shows the border line."""
    width, height, a = paint_display.read_ppm(clear)
    _, _, b = paint_display.read_ppm(hidden)
    count = total = 0
    for y in range(top, bottom):
        for x in range(here['x'], here['x'] + here['width']):
            i = (y * width + x) * 3
            total += 1
            count += max(abs(a[i + k] - b[i + k]) for k in range(3)) > BLEND
    return count, total


HIDES = 0.9  # share of the border line under a title the title must cover


def patch_pixels(clear, hidden, here, top, bottom):
    """Pixels of the title's glyph box, outside the border rows top..bottom it covers, where the
    title with transparent text differs from the capture with the title hidden by more than BLEND."""
    width, height, a = paint_display.read_ppm(clear)
    _, _, b = paint_display.read_ppm(hidden)
    count = total = 0
    for y in range(here['y'], here['y'] + here['height']):
        if top - 1 <= y <= bottom:
            continue
        for x in range(here['x'], here['x'] + here['width']):
            i = (y * width + x) * 3
            total += 1
            count += max(abs(a[i + k] - b[i + k]) for k in range(3)) > BLEND
    return count, total


def measure(m, shooter, name, selector, geometry, dpr, art=False):
    in_shadow = geometry['inShadow']
    hide = f'{selector}::before {{ visibility: hidden !important; }}'

    def css(rule):
        return ('', rule) if in_shadow else (rule, '')

    base = shooter.shoot()
    hidden = shooter.shoot(*css(hide))
    m.execute_script(HOSTS_JS + REFERENCE_JS, script_args=[name, True], sandbox='system')
    with_ref = shooter.shoot(*css(hide))
    m.execute_script(HOSTS_JS + REFERENCE_JS, script_args=[name, False], sandbox='system')
    again = shooter.shoot()
    if paint_display.diff(base, again, None, 2)[0]:
        raise RuntimeError(f'{name}: captures unstable')
    bg = geometry['bg']
    here = glyph_box(base, hidden, bg)
    rx, ry = int(REF_X * dpr) - 2, int(REF_Y * dpr) - 2
    ref = glyph_box(with_ref, hidden, bg, (rx, ry, rx + int(300 * dpr), ry + int(80 * dpr)))
    record = {'title': name, 'inPlace': here, 'reference': ref}
    failures = []
    if not here:
        return record, [f'{name}: title glyphs paint no pixels in place']
    if not ref:
        raise RuntimeError(f'{name}: reference title paints no glyph pixels')
    slack = round(dpr)
    if here['height'] + slack < ref['height'] or here['width'] + slack < ref['width']:
        failures.append(f'{name}: title is cut or covered: glyphs {here["width"]}x{here["height"]} painted, '
                        f'{ref["width"]}x{ref["height"]} whole')
    top = round(geometry['y'] * dpr)
    bottom = round((geometry['y'] + geometry['borderTop']) * dpr)
    if not (here['y'] < top and here['y'] + here['height'] > bottom):
        failures.append(f'{name}: title glyphs {here["y"]}..{here["y"] + here["height"]} do not straddle the top '
                        f'border {top}..{bottom}')
    hits = []
    for c in geometry['controls']:
        cx, cy = c['x'] * dpr, c['y'] * dpr
        cw, ch = c['w'] * dpr, c['h'] * dpr
        if (here['x'] + 1 < cx + cw and cx < here['x'] + here['width'] - 1
                and here['y'] + 1 < cy + ch and cy < here['y'] + here['height'] - 1):
            hits.append(c['id'])
    if hits:
        failures.append(f'{name}: title overlaps {hits[:3]}')
    record['overlaps'] = hits
    if art:
        clear = shooter.shoot(*css(f'{selector}::before {{ color: transparent !important; '
                                         'text-shadow: none !important; }}'))
        count, total = patch_pixels(clear, hidden, here, top, bottom)
        record['patch'] = {'pixels': count, 'of': total}
        if not total:
            raise RuntimeError(f'{name}: no title pixels outside the border rows to compare')
        if count:
            failures.append(f'{name}: title paints a patch over the theme art: {count} of {total} pixels differ '
                            'from the paint without it')
        covered, line = line_hidden(clear, hidden, here, top, bottom)
        record['lineHidden'] = {'pixels': covered, 'of': line}
        if covered < HIDES * line:
            failures.append(f'{name}: title shows the border line under it: covers {covered} of {line} pixels')
        return record, failures
    # Beside the title, inside the box: right of its padding, below the top border, above its bottom.
    x0 = here['x'] + here['width'] + round(8 * dpr)
    y0 = bottom + round(2 * dpr)
    y1 = here['y'] + here['height']
    beside = dominant(base, (x0, y0, x0 + round(16 * dpr), y1)) if y1 > y0 else None
    record['beside'] = beside
    if beside is None:
        raise RuntimeError(f'{name}: no box paint beside the title to compare')
    if max(abs(a - b) for a, b in zip(beside, bg)) > BLEND:
        failures.append(f'{name}: title background {bg} on box paint {beside}, not the same color')
    return record, failures


def run(display, nova, layout, dpp, expect_version, theme=None, art='none'):
    port = free_port()
    with tempfile.TemporaryDirectory(prefix='tf-titles-') as name:
        directory = Path(name)
        profile = directory / 'profile'
        profile.mkdir()
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        if layout == 'classic':
            # textfox hides the horizontal strip by default; the README's option shows it.
            (profile / 'chrome' / 'config.css').write_text(':root { --tf-display-horizontal-tabs: block; }\n'
                                                           + ff_themes.ART_CONFIG[art])
        elif ff_themes.ART_CONFIG[art]:
            (profile / 'chrome' / 'config.css').write_text(ff_themes.ART_CONFIG[art])
        prefs = {
            'toolkit.legacyUserProfileCustomizations.stylesheets': True,
            'browser.nova.enabled': nova,
            'browser.shell.checkDefaultBrowser': False,
            'browser.startup.page': 0,
            'browser.toolbars.bookmarks.visibility': 'always',
            'browser.newtabpage.activity-stream.feeds.section.topstories': False,
            'browser.newtabpage.activity-stream.showSponsoredTopSites': False,
            'ui.prefersReducedMotion': 1,
            'layout.css.devPixelsPerPx': str(dpp),
            'marionette.port': port,
            **LAYOUTS[layout],
            **(ff_themes.prefs(theme) if theme else {}),
        }
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        process = subprocess.Popen(display.firefox + ['--marionette', '-remote-allow-system-access', '--new-instance',
                                                      '--profile', str(profile), 'about:blank'],
                                   env={**display.firefox_env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   **display.popen_options())
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            wait(m, 'return gBrowserInit.delayedStartupFinished', 'Firefox startup')
            meta = m.execute_script(
                "return {version: Services.appinfo.version, build: Services.appinfo.appBuildID, dpr: devicePixelRatio,"
                " nova: Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if meta['nova'] != nova or abs(meta['dpr'] - dpp) > .01:
                raise RuntimeError(f'unexpected Firefox state: {meta}')
            if expect_version and meta['version'] != expect_version:
                raise RuntimeError(f'Firefox {meta["version"]}, expected {expect_version}')
            if theme:
                meta['theme'] = ff_themes.apply(m, theme)
            meta['art'] = ff_themes.art_expected(m, art)
            ff_themes.hold_still(m, art)
            m.execute_script("gBrowser.addTrustedTab('about:blank')", sandbox='system')
            if layout == 'classic':
                m.execute_script("SidebarController.show('viewBookmarksSidebar')", sandbox='system')
            m.execute_async_script("const done = arguments[0]; gBrowser.getFindBar().then(f => { f.open(); "
                                   "setTimeout(() => done(true), 500); })", sandbox='system')
            m.execute_script("document.activeElement?.blur(); gBrowser.selectedBrowser.focus()", sandbox='system')
            time.sleep(2)
            shooter = Shooter(m, display, directory)
            records, failures = [], []
            for title, (selector, lookup, layouts) in TITLES.items():
                if layout not in layouts:
                    continue
                geometry = m.execute_script(HOSTS_JS + GEOMETRY_JS, script_args=[title], sandbox='system')
                if geometry is None:
                    raise RuntimeError(f'{title}: box or title not shown in the {layout} layout')
                record, problems = measure(m, shooter, title, selector, geometry, meta['dpr'],
                                           art=meta['art']['expected'])
                records.append(record)
                failures += problems
            return meta, records, failures
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                display.stop_firefox(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--nova', choices=('false', 'true'))
    parser.add_argument('--layout', choices=tuple(LAYOUTS))
    parser.add_argument('--dpp', default='1,2', help='device pixel ratios, comma-separated')
    parser.add_argument('--expect-version')
    parser.add_argument('--theme', action='append', default=[], help='Firefox theme ID[:light|dark] (repeatable)')
    parser.add_argument('--themes', choices=('default', 'every', 'art'), help='theme set (see ff_themes.py)')
    parser.add_argument('--art', choices=tuple(ff_themes.ART_CONFIG), default='none',
                        help='turn --tf-theme-images or --tf-theme-gradient on (V19)')
    args = parser.parse_args()
    themes = args.theme + {'default': ff_themes.DEFAULT_THEMES, 'every': ff_themes.EVERY_THEME,
                           'art': ff_themes.ART_THEMES, None: []}[args.themes]
    for spec in themes:
        ff_themes.parse(spec)
    novas = [args.nova == 'true'] if args.nova else [False, True]
    layouts = [args.layout] if args.layout else list(LAYOUTS)
    dpps = [float(v) for v in args.dpp.split(',')]
    failures, judged_art = [], False
    with tempfile.TemporaryDirectory(prefix='tf-titles-sway-') as name:
        display = paint_display.open_display(name, dict(os.environ))
        try:
            runs = [(t, d, n, la) for t in themes or [None] for d in dpps for n in novas for la in layouts]
            for theme, dpp, nova, layout in runs:
                meta, records, problems = run(display, nova, layout, dpp, args.expect_version, theme, args.art)
                judged_art = judged_art or meta['art']['expected']
                print(json.dumps({'nova': nova, 'layout': layout, 'dpp': dpp, 'theme': theme, 'art': args.art,
                                  'firefox': meta,
                                  'titles': records}), flush=True)
                failures += [f'nova={nova} {layout} dpp={dpp} theme={theme}: {p}' for p in problems]
        except Exception as error:  # noqa: BLE001 - any harness failure fails the run
            for failure in failures:
                print(f'FAIL {failure}', flush=True)
            print(f'FAIL: {error}', flush=True)
            return 1
        finally:
            display.close()
    if args.art != 'none' and not judged_art:  # an --art run that judged nothing over art proves nothing
        failures.append(f'--art {args.art}: Firefox had no such art in any run; nothing was judged over art')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', flush=True)
        return 1
    states = '/'.join('on' if nova else 'off' for nova in novas)
    print('PASS: every section title paints whole on its top border, clear of controls, on its box\'s paint '
          f'(browser Nova {states}, {", ".join(layouts)}, dpr {args.dpp}'
          + (f', --art {args.art}' if args.art != 'none' else '')
          + (f', themes {", ".join(themes)}' if themes else '') + ')', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
