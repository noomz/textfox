#!/usr/bin/env python3
"""Check textfox's remaining surfaces from headed, painted pixels; exit 0 pass, 1 fail.

Extends test-surface-paint-live.py (T15) with the same harness, judge and
known-bad rule: a disposable headless Sway compositor, Firefox headed in it,
grim captures, and each surface hidden in turn to find what it painted.

Surfaces, in both browser Nova states:

- classic layout (sidebar.revamp false): bookmarks sidebar box, the content
  box (#tabbrowser-tabbox), both split-view panels, the unified extensions
  panel, the all-tabs panel, the page context menu, `about:` pages, and a
  private window's navbar, tab strip and content box plus about:privatebrowsing;
- revamp layout (sidebar.revamp and sidebar.verticalTabs true): bookmarks
  sidebar box, vertical tab strip, and a private window's navbar, vertical tab
  strip and content box.

Chrome surfaces must paint their own background variable (V22: --tf-sidebar-bg for the
sidebar, --tf-navbar-bg for the navbar, --tf-popup-bg for panels, else --tf-bg) as the dominant color (web content inside a
box is skipped), a border of their variable on the left, right and bottom
edges, and a textfox-colored text target at 4.5:1. The content box (sidebar
shown and hidden) and each split-view panel paint one frame (V17): between the
box's padding edge and its web content, every side's gap paints only --tf-bg,
no second line drawn by Firefox inside textfox's box. `about:` pages carry only
textfox's font (chrome/content/about.css), so each page gets two injected
probe runs, ten `i` and ten `W` with an inline serif font that textfox's
user-level `!important` rule must override: the painted runs must be about
equally wide (monospace) and their text must reach 4.5:1 against the painted
page.

Cases: shipped defaults, a sentinel config.css (or `--config FILE`), and a
known-bad config.css (text the color of the background, borders gone, a
proportional font). Every surface must fail the known-bad case, which runs once
per theme on a Nova state and layout rotating across themes, and on every one
with no theme (V23). Exits 0 or 1, never inconclusive.

`--theme ID[:light|dark]` (repeatable) or `--themes default|every` runs every
case with that Firefox theme active, as test-surface-paint-live.py does (V15).
`--art images|gradient` and `--themes art` work as there (V19): where Firefox itself
has that art, window boxes (sidebar, content box, split panels, tab strips, the
private window's boxes) are judged over the art (shown, text against the paint under
it), and the one-frame check compares each gap with a capture whose
`.browserContainer` is hidden instead of with --tf-bg. Popups keep the --tf-bg judge.

Requires sway, grim, ImageMagick, and the live-tool venv Python. Set
TF_SURFACE_ARTIFACTS to a directory to keep captures and masks.
"""
import argparse
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from ff_inspect import connect
import ff_themes

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
_paint = runpy.run_path(str(HERE / 'test-surface-paint-live.py'))
(Capture, Image, judge, art_under_some, wait, near, ratio, dominant, inflate, start_sway, stop, changed_mask, count,
 painted_box, RESOLVE_JS, CHROME_STYLE_JS, FREEZE, MIN_RATIO, MIN_TEXT_PIXELS, OUTPUT, EDGE_MATCH) = (
    _paint[k] for k in ('Capture', 'Image', 'judge', 'art_under_some', 'wait', 'near', 'ratio', 'dominant', 'inflate',
                        'start_sway', 'stop', 'changed_mask', 'count', 'painted_box', 'RESOLVE_JS',
                        'CHROME_STYLE_JS', 'FREEZE', 'MIN_RATIO', 'MIN_TEXT_PIXELS', 'OUTPUT', 'EDGE_MATCH'))

KNOWN_BAD_SIGNS = {'border', 'text', 'proportional'}  # words in the failures the known-bad config must cause
MONOSPACE = 0.8  # painted width of ten `i` over ten `W`; a proportional font paints about 0.3

TABS = '--tf-display-horizontal-tabs: block;'
CONFIGS = {
    'defaults': f':root {{ {TABS} }}\n',
    'sentinel': (f':root {{ {TABS} --tf-bg: rgb(24, 36, 64); --tf-border: rgb(200, 0, 90); '
                 '--tf-accent: rgb(0, 140, 90); }\n'),
    'known-bad': (f':root {{ {TABS} --tf-fg: var(--tf-bg); --tf-border-width: 0px; '
                  '--tf-navbar-fg: var(--tf-navbar-bg); --tf-urlbar-fg: var(--tf-urlbar-bg); '
                  '--tf-popup-fg: var(--tf-popup-bg); --tf-sidebar-fg: var(--tf-sidebar-bg); '
                  '--tf-tab-selected-fg: var(--tf-tab-selected-bg); --tf-font-family: serif; }'
                  # Over theme art, body sets the surface pairs (V19, chrome/browser.css).
                  ' body { --tf-navbar-fg: var(--tf-navbar-bg) !important;'
                  ' --tf-urlbar-fg: var(--tf-urlbar-bg) !important; --tf-sidebar-fg: var(--tf-sidebar-bg) !important; }\n'),
}
LAYOUTS = {
    'classic': {'sidebar.revamp': False, 'sidebar.verticalTabs': False},
    'revamp': {'sidebar.revamp': True, 'sidebar.verticalTabs': True},
}
ABOUT_PAGES = ['about:preferences', 'about:addons', 'about:config', 'about:support']

# name, selector hidden to find its paint, border variable, text target, web content skipped
SIDEBAR = ('sidebar', '#sidebar-box', '--tf-border', '#sidebar-box::before', None)
CONTENT_BOX = ('content-box', '#tabbrowser-tabbox', '--tf-border', '#tabbrowser-tabbox::before',
               '#tabbrowser-tabbox .browserStack')
NAVBAR = ('navbar', '#nav-bar', '--tf-border', '#nav-bar::before', None)
HORIZONTAL_TABS = ('tabs', '#TabsToolbar', '--tf-border', '.tabbrowser-tab:not([selected]) .tab-label', None)
VERTICAL_TABS = ('vertical-tabs', '#vertical-tabs', '--tf-border',
                 '#vertical-tabs .tabbrowser-tab:not([selected]) .tab-label', None)
SPLIT_PANELS = [
    ('split-active', '.split-view-panel.deck-selected', '--tf-border', '.split-view-panel.deck-selected::before',
     '.split-view-panel.deck-selected .browserStack'),
    ('split-other', '.split-view-panel:not(.deck-selected)', '--tf-border',
     '.split-view-panel:not(.deck-selected)::before', '.split-view-panel:not(.deck-selected) .browserStack'),
]
POPUPS = [
    ('extensions-panel', '#unified-extensions-panel', '--tf-accent', '#unified-extensions-panel .panel-header h1',
     "gUnifiedExtensions.togglePanel()",
     "return document.getElementById('unified-extensions-panel')?.state == 'open'",
     "PanelMultiView.hidePopup(document.getElementById('unified-extensions-panel'))"),
    ('all-tabs-panel', '#customizationui-widget-panel', '--tf-accent', '#allTabsMenu-searchTabs',
     "gTabsPanel.showAllTabsPanel()",
     "return document.getElementById('customizationui-widget-panel')?.state == 'open'",
     "PanelMultiView.hidePopup(document.getElementById('customizationui-widget-panel'))"),
]
# Each surface's own color pair (V22): background and text variables.
PAIRS = {'sidebar': ('--tf-sidebar-bg', '--tf-sidebar-fg'), 'navbar': ('--tf-navbar-bg', '--tf-navbar-fg'),
         'extensions-panel': ('--tf-popup-bg', '--tf-popup-fg'), 'all-tabs-panel': ('--tf-popup-bg', '--tf-popup-fg'),
         'page-context-menu': ('--tf-popup-bg', '--tf-popup-fg')}
WINDOW_PAIR = ('--tf-bg', '--tf-fg')  # tab strips, content box, split panels


def resolve(m, surface_name, selector):
    """RESOLVE_JS for a surface with its own pair (unprefixed name), or None if missing."""
    bg_var, fg_var = PAIRS.get(surface_name, WINDOW_PAIR)
    colors = m.execute_script(RESOLVE_JS, script_args=[selector, bg_var, fg_var], sandbox='system')
    if colors:
        colors['bgVar'] = bg_var
    return colors


PAGE_CONTEXT = ('page-context-menu', '#contentAreaContextMenu', '--tf-accent',
                '#contentAreaContextMenu > menuitem:not([hidden])', None)

SKIP_JS = r"""
const e = document.querySelector(arguments[0]);
if (!e) return null;
const r = e.getBoundingClientRect();
return {x:Math.floor(r.x), y:Math.floor(r.y), width:Math.ceil(r.width), height:Math.ceil(r.height)};
"""

# A box's padding edge (inside its textfox border) and the web content it shows, in client pixels.
GAP_JS = r"""
const panel = document.querySelector(arguments[0]);
if (!panel) return null;
const stack = panel.contains(gBrowser.selectedBrowser) ? gBrowser.selectedBrowser.closest('.browserStack')
  : panel.querySelector('.browserStack');
if (!stack) return null;
const r = panel.getBoundingClientRect(), s = getComputedStyle(panel), q = stack.getBoundingClientRect();
return {pad:{left:r.left + parseFloat(s.borderLeftWidth), right:r.right - parseFloat(s.borderRightWidth),
             top:r.top + parseFloat(s.borderTopWidth), bottom:r.bottom - parseFloat(s.borderBottomWidth)},
        stack:{left:q.left, right:q.right, top:q.top, bottom:q.bottom}};
"""


def frame_lines(image, gap, background, reference=None):
    """Sides whose gap between padding edge and web content holds a line that is not background.

    Left and right use the middle half of the content's rows; bottom uses columns 10% to 50% of
    its width, clear of textfox's split-view footer in the bottom-right corner; top uses 50% to
    90%, clear of the section title at the top-left. With a reference capture (theme art), a
    pixel counts where it differs from the reference rather than from background."""
    pad, stack = gap['pad'], gap['stack']
    top, height = stack['top'], stack['bottom'] - stack['top']
    left, width = stack['left'], stack['right'] - stack['left']
    rows = range(math.ceil(top + height / 4), math.floor(top + height * 3 / 4))
    columns = range(math.ceil(left + width / 10), math.floor(left + width / 2))
    top_columns = range(math.ceil(left + width / 2), math.floor(left + width * 9 / 10))
    strips = {
        'top': [[(x, y) for x in top_columns] for y in range(math.ceil(pad['top']), math.floor(stack['top']))],
        'left': [[(x, y) for y in rows] for x in range(math.ceil(pad['left']), math.floor(stack['left']))],
        'right': [[(x, y) for y in rows] for x in range(math.ceil(stack['right']), math.floor(pad['right']))],
        'bottom': [[(x, y) for x in columns] for y in range(math.ceil(stack['bottom']), math.floor(pad['bottom']))],
    }
    def off(x, y):
        return not near(image.pixel(x, y), reference.pixel(x, y) if reference else background)
    return {side: sum(1 for line in lines
                      if line and sum(off(x, y) for x, y in line) >= EDGE_MATCH * len(line))
            for side, lines in strips.items()}


# Two runs with an inline serif font; textfox's user-level !important font rule must win.
PROBE_JS = r"""
document.getElementById('tf-font-probe')?.remove();
const box = document.createElement('div');
box.id = 'tf-font-probe';
box.style.cssText = 'position:fixed;top:45%;inset-inline-end:24px;z-index:2147483647;' +
  'font-size:32px;line-height:1.4;font-family:serif;background:none;border:0;margin:0;padding:0';
for (const [id, text] of [['tf-probe-i', 'iiiiiiiiii'], ['tf-probe-w', 'WWWWWWWWWW']]) {
  const run = document.createElement('div');
  run.id = id;
  run.textContent = text;
  run.style.cssText = 'font-family:serif;white-space:pre';
  box.appendChild(run);
}
(document.body || document.documentElement).appendChild(box);
const done = arguments[arguments.length - 1];
requestAnimationFrame(() => requestAnimationFrame(() => done({
  href: location.href, font: getComputedStyle(document.getElementById('tf-probe-i')).fontFamily})));
"""


class Shots(Capture):
    """T15 capture, with styles loaded as an author sheet in content too: about: pages refuse inline <style>."""

    def shoot(self, css, content):
        self.m.execute_async_script(CHROME_STYLE_JS, script_args=[FREEZE + css], sandbox='system')
        time.sleep(.5)
        self.shots += 1
        path = self.directory / f'shot-{self.shots}.ppm'
        for attempt in range(3):  # grim fails now and then while a window maps or unmaps
            if subprocess.run(['grim', '-t', 'ppm', str(path)], env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                return path
            time.sleep(1)
        raise RuntimeError(f'grim could not capture {path}')


def diff_box(a, b):
    """Bounding box and pixel list of pixels that differ between two captures."""
    xs, ys, pixels = [], [], []
    for y in range(a.height):
        row = y * a.width * 3
        if a.data[row:row + a.width * 3] == b.data[row:row + a.width * 3]:
            continue
        for x in range(a.width):
            if a.pixel(x, y) != b.pixel(x, y):
                xs.append(x)
                ys.append(y)
                pixels.append((x, y))
    if not xs:
        return None, []
    return {'x': min(xs), 'y': min(ys), 'width': max(xs) - min(xs) + 1, 'height': max(ys) - min(ys) + 1}, pixels


def judge_font(name, shot):
    """Monospace runs readable against the painted page; return (record, failures)."""
    base, run_w = shot['image'], shot['text']
    box_i = shot['box']
    box_w, w_pixels = diff_box(base, run_w)
    record = {'surface': name, 'iBox': box_i, 'wBox': box_w}
    if not box_i or not box_w:
        return record, [f'{name} font probe paints no pixels']
    record['widthRatio'] = round(box_i['width'] / box_w['width'], 3)
    failures = []
    if record['widthRatio'] < MONOSPACE:
        failures.append(f'{name} paints a proportional font: ten i are {box_i["width"]}px, '
                        f'ten W are {box_w["width"]}px')
    background = dominant(base, inflate(box_w, 8))
    strong = sum(1 for x, y in w_pixels if ratio(base.pixel(x, y), background) >= MIN_RATIO)
    record.update(background=background, textPixelsAtRatio=strong)
    if strong < MIN_TEXT_PIXELS:
        failures.append(f'{name} probe text paints {strong} pixels at {MIN_RATIO}:1, fewer than {MIN_TEXT_PIXELS}')
    return record, failures


class Run:
    def __init__(self, m, capture, art=False):
        self.m, self.capture, self.art = m, capture, art
        self.records, self.failures = [], {}

    def chrome(self, surface, prefix='', over_art=True):
        name, selector, border_var, text_selector, skip = surface
        name = prefix + name
        self.m.set_context('chrome')
        colors = resolve(self.m, surface[0], selector)
        if not colors:
            raise RuntimeError(f'{name}: {selector} missing')
        if skip:
            colors['skip'] = self.m.execute_script(SKIP_JS, script_args=[skip], sandbox='system')
            if not colors['skip']:
                raise RuntimeError(f'{name}: {skip} missing')
        if surface[0] == 'navbar':
            # The URL bar field paints its own pair (V22; over art the theme's own field, B27), which
            # can fill most of the navbar (a Nova private window): the navbar is judged around it.
            field = self.capture.surface(name + ' urlbar-field', '.urlbar-background')['box']
            if field:
                colors['skip'] = field
        # Only over art: a popup's --tf-bg can sit within noise of faint art behind it.
        self.capture.noise = _paint['CONTROL_NOISE'] if self.art and over_art else 0
        self.capture.box_noise = _paint['CONTROL_NOISE'] if self.art else 0  # B29
        shot = self.capture.surface(name, selector, text_selector)
        self.capture.noise = self.capture.box_noise = 0
        box = shot['box']
        # Client rects are used as compositor pixels; Wayland's mozInnerScreenX counts the CSD shadow, so
        # check placement directly: the skipped web content must sit inside the surface's painted box.
        # A box smaller than the content paints almost nothing (the known-bad config); judge reports that.
        skip_rect = colors.get('skip')
        if (skip and box and box['width'] >= skip_rect['width'] and box['height'] >= skip_rect['height']
                and not (box['x'] <= colors['skip']['x'] and box['y'] <= colors['skip']['y']
                                 and colors['skip']['x'] + colors['skip']['width'] <= box['x'] + box['width']
                                 and colors['skip']['y'] + colors['skip']['height'] <= box['y'] + box['height'])):
            raise RuntimeError(f'{name}: content rect {colors["skip"]} outside painted box {box}')
        record, self.failures[name] = judge(name, shot, colors, border_var=border_var, art=self.art and over_art)
        self.records.append(record)

    def single_frame(self, surface, state='', record_name=None):
        """V17: no line inside the box's textfox border other than its web content's own pixels.

        Failures go on record_name's surface (default: this one), so the known-bad rule still sees
        the same surfaces."""
        name, selector = surface[0], surface[1]
        record_name = record_name or name
        self.m.set_context('chrome')
        gap = self.m.execute_script(GAP_JS, script_args=[selector], sandbox='system')
        if not gap:
            raise RuntimeError(f'{name}: {selector} .browserStack missing')
        background = resolve(self.m, surface[0], selector)['bg']
        image = Image(self.capture.shoot('', False), self.capture.size)
        reference = None
        if self.art:
            reference = Image(self.capture.shoot(f'{selector} .browserContainer {{ visibility: hidden !important; }}',
                                                 False), self.capture.size)
        lines = frame_lines(image, gap, tuple(background), reference)
        pad, stack = gap['pad'], gap['stack']
        widths = {'left': stack['left'] - pad['left'], 'right': pad['right'] - stack['right'],
                  'top': stack['top'] - pad['top'], 'bottom': pad['bottom'] - stack['bottom']}
        if any(w < 2 for w in widths.values()):
            raise RuntimeError(f'{name}: gap between padding edge and content too narrow to judge: {widths}')
        label = f'{name}{state}'
        record = next(r for r in self.records if r['surface'] == record_name)
        record.setdefault('frames', {})[label] = {'gap': gap, 'lines': lines}
        extra = [side for side, n in lines.items() if n]
        if extra:
            self.failures[record_name].append(f'{label} paints a second frame inside its box on '
                                              f'{", ".join(extra)} ({lines} lines not --tf-bg {background})')

    def font(self, url, prefix=''):
        name = f'{prefix}font {url}'
        select_content(self.m)
        if url != 'about:privatebrowsing':
            self.m.navigate(url)
        wait(self.m, f"return location.href.startsWith({json.dumps(url)}) && document.readyState == 'complete'",
             f'{url} load', sandbox='default')
        time.sleep(1)
        probe = self.m.execute_async_script(PROBE_JS, sandbox='default')
        shot = self.capture.surface(name, '#tf-probe-i', '#tf-probe-w', content=True)
        record, self.failures[name] = judge_font(name, shot)
        record['computedFont'] = probe['font']
        if not probe['href'].startswith(url):
            raise RuntimeError(f'{name}: probe ran on {probe["href"]}')
        record['href'] = probe['href']
        self.records.append(record)
        self.m.set_context('chrome')


def select_content(m):
    """Point Marionette's content context at the selected tab."""
    m.set_context('chrome')
    selected = m.execute_script('return gBrowser.selectedBrowser.browserId', sandbox='system')
    m.set_context('content')
    for handle in m.window_handles:
        m.switch_to_window(handle, focus=False)
        if m.execute_script('return window.browsingContext.browserId', sandbox='system') == selected:
            m.switch_to_window(handle)
            return
    raise RuntimeError(f'no content handle for selected browser {selected}')


def wait_open(m, script, what):
    wait(m, script, what)
    time.sleep(1)


def neutral_pointer(m):
    """Park the pointer in the window's top-left margin so no bordered box shows its hover color."""
    m.set_context('chrome')
    m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(2, 2, origin='viewport').perform()


def open_sidebar(m):
    m.execute_script("SidebarController.show('viewBookmarksSidebar')", sandbox='system')
    wait_open(m, "const b = document.getElementById('sidebar-box'); return !b.hidden && "
              "document.getElementById('sidebar').contentDocument?.readyState == 'complete'", 'bookmarks sidebar')
    # The sidebar focuses its search field; its caret blinks in a document the freeze sheet does not reach.
    m.execute_script("gBrowser.selectedBrowser.focus()", sandbox='system')
    wait_open(m, "return !document.getElementById('sidebar').contentDocument.hasFocus()", 'sidebar unfocused')


def classic(m, run):
    open_sidebar(m)
    run.chrome(SIDEBAR)
    run.single_frame(CONTENT_BOX, ' (sidebar shown)', record_name=SIDEBAR[0])
    m.execute_script("SidebarController.hide()", sandbox='system')
    wait_open(m, "return document.getElementById('sidebar-box').hidden", 'sidebar hidden')
    run.chrome(CONTENT_BOX)
    run.single_frame(CONTENT_BOX)
    run.chrome(HORIZONTAL_TABS)

    m.execute_script("const first = gBrowser.selectedTab; "
                     "gBrowser.addTabSplitView([first, gBrowser.addTrustedTab('about:blank')], {insertBefore:first})",
                     sandbox='system')
    wait_open(m, "return gBrowser.tabpanels.splitViewPanels?.length === 2 && "
              "document.querySelectorAll('.split-view-panel').length === 2", 'split view')
    for surface in SPLIT_PANELS:
        run.chrome(surface)
        run.single_frame(surface)
    m.execute_script("const t = gBrowser.selectedTab; gBrowser.selectedTab = gBrowser.addTrustedTab('about:blank'); "
                     "for (const s of t.splitview.tabs) gBrowser.removeTab(s)", sandbox='system')
    wait_open(m, "return !document.querySelector('.split-view-panel')", 'split view closed')

    for name, selector, border_var, text_selector, opener, is_open, closer in POPUPS:
        m.execute_script(opener, sandbox='system')
        wait_open(m, is_open, f'{name} open')
        run.chrome((name, selector, border_var, text_selector, None), over_art=False)
        m.execute_script(closer, sandbox='system')
        wait_open(m, is_open.replace("== 'open'", "!= 'open'"), f'{name} closed')

    select_content(m)
    m.navigate('data:text/html,<p style="font-size:24px">textfox context probe</p>')
    m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).click(
        m.find_element('css selector', 'p'), button=2).perform()
    m.set_context('chrome')
    wait_open(m, "return document.getElementById('contentAreaContextMenu').state == 'open'", 'page context menu')
    neutral_pointer(m)  # the menu opens under the pointer; keep its first item unhovered
    wait(m, "return document.getElementById('contentAreaContextMenu').state == 'open' && "
         "!document.querySelector('#contentAreaContextMenu > [_moz-menuactive]')", 'context menu unhovered')
    run.chrome(PAGE_CONTEXT, over_art=False)
    m.execute_script("document.getElementById('contentAreaContextMenu').hidePopup()", sandbox='system')
    wait_open(m, "return document.getElementById('contentAreaContextMenu').state == 'closed'", 'context menu closed')
    neutral_pointer(m)
    for url in ABOUT_PAGES:
        run.font(url)


def revamp(m, run):
    m.execute_script("SidebarController.promiseInitialized.then(() => "
                     "SidebarController.updateUIState({hidden:false, launcherExpanded:true}))", sandbox='system')
    wait_open(m, "return document.getElementById('vertical-tabs')?.hasAttribute('visible')", 'vertical tabs')
    run.chrome(VERTICAL_TABS)
    open_sidebar(m)
    run.chrome(SIDEBAR)
    run.single_frame(CONTENT_BOX, ' (sidebar shown)', record_name=SIDEBAR[0])
    m.execute_script("SidebarController.hide()", sandbox='system')
    wait_open(m, "return document.getElementById('sidebar-box').hidden", 'sidebar hidden')
    run.chrome(CONTENT_BOX)
    run.single_frame(CONTENT_BOX)


def private(m, run, layout, art='none'):
    """Open a private window, close the normal one so it fills the output, and measure it."""
    neutral_pointer(m)
    normal = m.current_chrome_window_handle
    m.execute_script("OpenBrowserWindow({private: true})", sandbox='system')
    deadline = time.monotonic() + 30
    while len(m.chrome_window_handles) < 2:
        if time.monotonic() >= deadline:
            raise RuntimeError('private window did not open')
        time.sleep(.2)
    handle = next(h for h in m.chrome_window_handles if h != normal)
    m.close_chrome_window()
    m.switch_to_window(handle)
    neutral_pointer(m)  # the new window maps under the pointer; park it again
    wait(m, "return gBrowserInit.delayedStartupFinished && PrivateBrowsingUtils.isWindowPrivate(window) && "
         "gBrowser.currentURI.spec == 'about:privatebrowsing'", 'private window startup')
    m.execute_script("document.documentElement.removeAttribute('remotecontrol'); "
                     "gBrowser.selectedTab = gBrowser.addTrustedTab('about:privatebrowsing')", sandbox='system')
    if layout == 'revamp':
        wait_open(m, "return document.getElementById('vertical-tabs')?.hasAttribute('visible') && "
                  "gBrowser.tabs.length == 2", 'private vertical tabs')
    else:
        wait_open(m, "return gBrowser.tabs.length == 2", 'private second tab')
    ff_themes.hold_still(m, art)
    # Whether Firefox has the art in this window too (V19): its own value, as for the first window.
    run.private_art = ff_themes.art_expected(m, art)
    run.art = run.private_art['expected']
    # The new window focuses its URL bar, whose focus ring (B12) would come and go between captures.
    m.execute_script("gBrowser.selectedBrowser.focus()", sandbox='system')
    time.sleep(.5)
    for surface in (NAVBAR, VERTICAL_TABS if layout == 'revamp' else HORIZONTAL_TABS, CONTENT_BOX):
        run.chrome(surface, prefix='private ')
    run.font('about:privatebrowsing', prefix='private ')


def launch(env, nova, layout, config, kind, theme=None, art='none'):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-surface-more-') as name:
        directory = Path(name)
        profile = directory / 'profile'
        profile.mkdir()
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(config)
        prefs = {
            'toolkit.legacyUserProfileCustomizations.stylesheets': True,
            'browser.nova.enabled': nova,
            'browser.shell.checkDefaultBrowser': False,
            'browser.startup.page': 0,
            'browser.aboutConfig.showWarning': False,
            'browser.newtabpage.activity-stream.widgets.enabled': False,
            'browser.newtabpage.activity-stream.showWeather': False,
            'browser.newtabpage.activity-stream.feeds.section.topstories': False,
            'browser.newtabpage.activity-stream.showSponsoredTopSites': False,
            'ui.prefersReducedMotion': 1,
            'marionette.port': port,
            **LAYOUTS[layout],
            **(ff_themes.prefs(theme) if theme else {}),
        }
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        shots = directory
        if os.environ.get('TF_SURFACE_ARTIFACTS'):
            shots = Path(os.environ['TF_SURFACE_ARTIFACTS']) / f'more-nova-{str(nova).lower()}-{layout}-{kind}'
            shots.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env={**env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, "
                "buildID:Services.appinfo.appBuildID, wayland:Services.env.get('WAYLAND_DISPLAY'), "
                "headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless, dpr:devicePixelRatio, "
                "browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                "revamp:Services.prefs.getBoolPref('sidebar.revamp'), "
                "verticalTabs:Services.prefs.getBoolPref('sidebar.verticalTabs')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['headless'] or not meta['wayland'] or meta['browserNova'] != nova or meta['dpr'] != 1
                    or meta['revamp'] != LAYOUTS[layout]['sidebar.revamp']
                    or meta['verticalTabs'] != LAYOUTS[layout]['sidebar.verticalTabs']):
                raise RuntimeError(f'unexpected Firefox process, profile, display or preference: {meta}')
            wait(m, 'return gBrowserInit.delayedStartupFinished', 'Firefox startup')
            if theme:
                meta['theme'] = ff_themes.apply(m, theme)
            meta['art'] = ff_themes.art_expected(m, art)
            ff_themes.hold_still(m, art)
            m.execute_script("document.documentElement.removeAttribute('remotecontrol'); "
                             "gBrowser.addTrustedTab('about:blank')", sandbox='system')
            wait_open(m, 'return gBrowser.tabs.length == 2', 'second tab')
            neutral_pointer(m)
            run = Run(m, Shots(m, shots, env), art=meta['art']['expected'])
            (classic if layout == 'classic' else revamp)(m, run)
            private(m, run, layout, art)
            meta['privateArt'] = run.private_art
            missing_art = art_under_some(run.records)
            if missing_art:
                run.failures[next(r['surface'] for r in run.records if 'art' in r)].append(missing_art)
            return meta, run.records, run.failures
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                stop(process, 15)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, help='config.css to check in place of the sentinel config')
    parser.add_argument('--layout', choices=list(LAYOUTS), action='append', help='limit to a layout (repeatable)')
    parser.add_argument('--nova', choices=['false', 'true'], action='append', help='limit to a browser Nova state')
    parser.add_argument('--theme', action='append', default=[], help='Firefox theme ID[:light|dark] (repeatable)')
    parser.add_argument('--themes', choices=('default', 'every', 'art'), help='theme set (see ff_themes.py)')
    parser.add_argument('--art', choices=tuple(ff_themes.ART_CONFIG), default='none',
                        help='turn --tf-theme-images or --tf-theme-gradient on (V19)')
    args = parser.parse_args()
    themes = args.theme + {'default': ff_themes.DEFAULT_THEMES, 'every': ff_themes.EVERY_THEME,
                           'art': ff_themes.ART_THEMES, None: []}[args.themes]
    for spec in themes:
        ff_themes.parse(spec)
    configs = dict(CONFIGS)
    if args.config:
        del configs['sentinel']
        configs['given'] = args.config.read_text()
    configs = {k: v + ff_themes.ART_CONFIG[args.art] for k, v in configs.items()}
    layouts = args.layout or list(LAYOUTS)
    novas = [n == 'true' for n in args.nova] if args.nova else [False, True]
    failures, surfaces, judged_art = [], {}, False
    with tempfile.TemporaryDirectory(prefix='tf-surface-sway-') as name:
        directory = Path(name)
        directory.chmod(0o700)
        env = os.environ.copy()
        sway = start_sway(directory, env)
        try:
            runs = ff_themes.suite_runs(themes, [(n, la) for n in novas for la in layouts], configs)
            for theme, nova, layout, kind in runs:
                config = configs[kind]
                meta, records, found = launch(env, nova, layout, config, kind, theme, args.art)
                judged_art = judged_art or meta['art']['expected']
                print(json.dumps({'nova': nova, 'layout': layout, 'config': kind, 'theme': theme, 'art': args.art,
                                  'firefox': meta, 'surfaces': records}), flush=True)
                surfaces.setdefault(layout, list(found))
                if list(found) != surfaces[layout]:
                    raise RuntimeError(f'{layout}: surfaces measured differ: {list(found)}')
                if kind == 'known-bad':
                    # The known-bad config hides text, borders and the font; other failures do not count.
                    # Over theme art (V19) a box drops its --tf-bg: on a theme whose frame is black like
                    # its text, it then paints nothing at all, and its border, the color of the
                    # background, reads as indistinguishable from it.
                    over_art = meta['art']['expected']
                    undetected = [s for s, problems in found.items()
                                  if not any(KNOWN_BAD_SIGNS & set(p.split()) or over_art and (
                                      p.endswith('paints no pixels') or 'indistinguishable from its background' in p)
                                             for p in problems)]
                    if undetected:
                        raise RuntimeError(f'nova={nova} layout={layout} theme={theme}: known-bad config.css not '
                                           'detected on ' + ', '.join(undetected))
                    continue
                failures += [f'nova={nova} layout={layout} theme={theme} config={kind}: {p}'
                             for problems in found.values() for p in problems]
        finally:
            stop(sway, 10)
    if args.art != 'none' and not judged_art:  # an --art run that judged nothing over art proves nothing
        failures.append(f'--art {args.art}: Firefox had no such art in any run; nothing was judged over art')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        return 1
    total = sum(len(v) for v in surfaces.values())
    good = ', '.join(k for k in configs if k != 'known-bad')
    print(f'PASS: {total} surfaces across {", ".join(layouts)} paint textfox styling in browser Nova '
          f'{"/".join(str(n).lower() for n in novas)} ({good})'
          + (f' with --art {args.art}' if args.art != 'none' else '')
          + '; the known-bad config.css fails every one')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # an untrustworthy run is a failure, never inconclusive
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
