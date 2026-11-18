#!/usr/bin/env python3
"""Check textfox's keyboard focus, RTL and reduced-motion output from headed, painted pixels.

Exit 0 pass, 1 fail; never inconclusive. Uses the T15 harness
(test-surface-paint-live.py): a disposable 1280x800 headless Sway compositor,
Firefox headed in it, grim captures.

Checks, in both browser Nova states:

- focus (en and he locales): trusted key presses (nsITextInputProcessor: F6,
  Shift+Tab, ArrowDown) move keyboard focus to the URL bar, a navbar button
  before it, the selected tab and an app menu item; the findbar field takes
  keyboard-flagged focus; ArrowDown activates a toolbar context menu item.
  The painted difference between the focused and unfocused capture, near the
  target, must surround the target and cross all four sides with pixels that
  change at 3:1 or more (WCAG 2.4.13; a ring or a filled indicator both
  pass). The context menu item needs a distinct active background over at
  least half the row.
- rtl (he locale, the signed Hebrew language pack, see
  test-rtl-locale-live.py): the T15 chrome surfaces (navbar, URL bar text and
  results, tab strip, findbar, app menu, context menu) must paint textfox's
  background, border and 4.5:1 text. Mirroring, from painted boxes: the app
  menu button paints left of the URL bar, the second tab left of the first.
- motion (en and he locales, ui.prefersReducedMotion 1): nothing may animate
  at rest; with hover locked on the navbar and tab strip, no transition or
  animation may start, and the first captured frame must already paint the
  hovered --tf-accent border on the left, right and bottom edges.

Cases: shipped defaults, a sentinel config.css with distinct colors (or
`--config FILE`), and one known-bad config.css per check: --tf-accent the
color of the background with active menu backgrounds removed; a forced 4s
border transition; for RTL, text the color of the background with no
borders (paint surfaces) and, separately, a left-to-right navbar and tab
strip (mirror surfaces). Every surface must pass the good cases and fail its
known-bad case with the failure the check exists for.

Requires sway, grim, ImageMagick, the live-tool venv Python and the Hebrew
language pack. New-tab feeds are on, so the profile may make network requests.
Set TF_SURFACE_ARTIFACTS to a directory to keep captures.
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

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
_paint = runpy.run_path(str(HERE / 'test-surface-paint-live.py'))
(Capture, Image, judge, wait, near, ratio, inflate, border_sides, start_sway, stop, changed_mask, count,
 painted_box, RESOLVE_JS, CHROME_STYLE_JS, CHROME_SURFACES, DISTINCT, OUTPUT) = (
    _paint[k] for k in ('Capture', 'Image', 'judge', 'wait', 'near', 'ratio', 'inflate', 'border_sides',
                        'start_sway', 'stop', 'changed_mask', 'count', 'painted_box', 'RESOLVE_JS', 'CHROME_STYLE_JS',
                        'CHROME_SURFACES', 'DISTINCT', 'OUTPUT'))
SURFACE_PAIRS = _paint['SURFACE_PAIRS']


def resolve(m, name, selector):
    """RESOLVE_JS with the surface's own pair (V22); window boxes take --tf-bg."""
    bg_var, fg_var = SURFACE_PAIRS.get(name, ('--tf-bg', '--tf-fg'))[:2]
    colors = m.execute_script(RESOLVE_JS, script_args=[selector, bg_var, fg_var], sandbox='system')
    if colors:
        colors['bgVar'] = bg_var
    return colors


langpack_for, LANGPACK_ID = (runpy.run_path(str(HERE / 'test-rtl-locale-live.py'))[k]
                             for k in ('langpack_for', 'LANGPACK_ID'))

FOCUS_RATIO = 3.0   # WCAG 2.4.13: focused against unfocused pixels
RING_BAND = 8       # px inward from the changed region's edge where the ring must cross
RING_MATCH = 0.8    # share of rows or columns across a side's middle half the ring must cross
RING_SPAN = 0.6    # least share of the target's width and height a ring must span
FILL_MATCH = 0.5    # share of a menu row that must change for an active background
NEAR_TARGET = 0.5   # share of all changed pixels that must lie near the target, or the measurement is off
PAD = 6             # px around the target's box where its indicator may paint

TABS = '--tf-display-horizontal-tabs: block;'
GOOD = {
    'defaults': f':root {{ {TABS} }}\n',
    # Distinct colors; the accent (focus rings, hover) reads at 3:1 against background and border.
    'sentinel': (f':root {{ {TABS} --tf-bg: rgb(24, 36, 64); --tf-border: rgb(200, 0, 90); '
                 '--tf-accent: rgb(250, 200, 0); }\n'),
}
MIRRORED = lambda surface: surface.startswith('mirrored-')
# name: (check, config.css, surfaces it must fail, failures that count as detection). Any other
# failure does not count: a mirror check must catch the direction, not an icon that paints nothing.
KNOWN_BAD = {
    # Focus rings take --tf-accent: an accent the color of the background hides them.
    'focus': ('focus', f':root {{ {TABS} --tf-accent: var(--tf-bg); }}\n'
              'menu, menuitem { background: transparent !important; }\n',
              lambda surface: True, ('paints no keyboard focus indicator', 'below 3.0:1', 'active background')),
    'motion': ('motion', f':root {{ {TABS} --tf-border-transition: 4s linear !important; }}\n',
               lambda surface: True, ('starts', 'lacks settled')),
    'rtl-paint': ('rtl', f':root {{ {TABS} --tf-fg: var(--tf-bg); --tf-border-width: 0px; }}\n',
                  lambda surface: not MIRRORED(surface), ('paints background', 'border', 'text paints')),
    'rtl-direction': ('rtl', f':root {{ {TABS} }}\n#nav-bar, #tabbrowser-tabs {{ direction: ltr !important; }}\n',
                      MIRRORED, ('not mirrored',)),
}
CHECKS_BY_LOCALE = {'en': ('focus', 'motion'), 'he': ('focus', 'motion', 'rtl')}

KEY_JS = r"""
const tip = Cc['@mozilla.org/text-input-processor;1'].createInstance(Ci.nsITextInputProcessor);
if (!tip.beginInputTransactionForTests(window)) throw new Error('text input processor unavailable');
const [key, code, keyCode, shift] = arguments[0];
const SHIFT = new KeyboardEvent('', {key: 'Shift', code: 'ShiftLeft', keyCode: 16});
if (shift) tip.keydown(SHIFT);
const event = new KeyboardEvent('', {key, code, keyCode});
tip.keydown(event);
tip.keyup(event);
if (shift) tip.keyup(SHIFT);
"""
F6, BACK_TAB, DOWN = ('F6', 'F6', 117, False), ('Tab', 'Tab', 9, True), ('ArrowDown', 'ArrowDown', 40, False)
ACTIVE_JS = r"""
const a = document.activeElement;
return a && a.matches(arguments[0]) ? (a.id || a.localName) : null;
"""
RECT_JS = r"""
const e = arguments[0] === ':active-element' ? document.activeElement : document.querySelector(arguments[0]);
if (!e) return null;
const r = e.getBoundingClientRect();
return {id: e.id || e.localName, x: r.x, y: r.y, width: r.width, height: r.height};
"""
# name, focus action, selector the focused element must match, target box selector, indicator kind
FOCUS_SURFACES = [
    ('urlbar', ('keys', F6), '#urlbar-input', '#urlbar', 'ring'),
    ('navbar-button', ('keys', BACK_TAB), '#nav-bar toolbarbutton', ':active-element', 'ring'),
    ('tab', ('keys', BACK_TAB), '.tabbrowser-tab', ':active-element', 'ring'),
    ('findbar-field', ('script', "Services.focus.setFocus(document.querySelector('findbar:not([hidden]) "
                                 ".findbar-textbox'), Services.focus.FLAG_BYKEY)"),
     '.findbar-textbox', ':active-element', 'ring'),
]
POPUP_FOCUS = [
    ('app-menu-item', "PanelUI.show()", "return document.getElementById('appMenu-popup').state == 'open'",
     '#appMenu-new-tab-button2', 'ring', "PanelUI.hide()"),
    ('context-menu-item', "document.getElementById('toolbar-context-menu')"
     ".openPopup(document.getElementById('nav-bar'), 'after_start')",
     "return document.getElementById('toolbar-context-menu').state == 'open'",
     '#toolbar-context-menu > [_moz-menuactive]', 'fill',
     "document.getElementById('toolbar-context-menu').hidePopup()"),
]
# Firefox's own findbar transition overrides textfox's there, so the findbar is not a motion surface.
MOTION_SURFACES = [('navbar', '#nav-bar'), ('tabs', '#TabsToolbar')]
ANIMATIONS_JS = r"""
return document.getAnimations().filter(a => a.playState === 'running').map(a => {
  const t = a.effect?.target, timing = a.effect?.getComputedTiming() || {};
  return {type: a.constructor.name, property: a.transitionProperty || a.animationName || null,
          target: t ? (t.id || t.localName) : null, duration: timing.duration};
});
"""
HOVER_JS = r"""
const done = arguments[arguments.length - 1];
const e = document.querySelector(arguments[0]);
const before = new Set(document.getAnimations());
if (arguments[1]) InspectorUtils.addPseudoClassLock(e, ':hover');
else { InspectorUtils.removePseudoClassLock(e, ':hover'); for (const a of e.getAnimations({subtree: true})) a.finish(); }
requestAnimationFrame(() => requestAnimationFrame(() => done(document.getAnimations()
  .filter(a => !before.has(a) && a.playState === 'running').map(a => {
    const t = a.effect?.target;
    return {type: a.constructor.name, property: a.transitionProperty || a.animationName || null,
            target: t ? (t.id || t.localName) : null, duration: a.effect?.getComputedTiming().duration};
  }))));
"""


NOISE = '0.8%'  # gray difference at or below 2 of 255 is rendering noise, not a change


class StableCapture(Capture):
    """T15's Capture, with stability controls that ignore differences of 2/255 or less.

    Text the color of the background (the known-bad RTL paint config) still renders
    antialiasing that differs by one unit between captures; the judge's own color
    tolerance is 4, so such noise cannot make a surface pass or fail."""

    def surface(self, name, selector, text_selector=None, content=False):
        hide = '{ visibility: hidden !important; }'
        base = self.shoot('', content)
        control = self.shoot('', content)
        hidden = self.shoot(f'{selector} {hide}', content)
        text = self.shoot(f'{text_selector} {hide}', content) if text_selector else None
        restored = self.shoot('', content)
        stem = self.directory / f'mask-{self.shots}'
        controls = {}
        for label, other in (('control', control), ('restored', restored)):
            mask = f'{stem}-{label}.png'
            subprocess.run(['magick', str(base), str(other), '-compose', 'difference', '-composite',
                            '-colorspace', 'gray', '-threshold', NOISE, mask], check=True)
            controls[label] = count(mask)
        if any(controls.values()):
            raise RuntimeError(f'{name}: captures unstable {controls}')
        mask = changed_mask(base, hidden, f'{stem}-{name}.png')
        return {'image': Image(base), 'box': painted_box(mask), 'changed': count(mask),
                'text': Image(text) if text else None}


def box_of(rect):
    x, y = max(0, int(rect['x'])), max(0, int(rect['y']))
    right = min(OUTPUT[0], int(rect['x'] + rect['width'] + .999))
    bottom = min(OUTPUT[1], int(rect['y'] + rect['height'] + .999))
    return {'x': x, 'y': y, 'width': right - x, 'height': bottom - y}


def bounds(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return {'x': min(xs), 'y': min(ys), 'width': max(xs) - min(xs) + 1, 'height': max(ys) - min(ys) + 1}


def ring_sides(before, after, box):
    """Sides of box where the ring changes at FOCUS_RATIO.

    Each row (left, right) or column (top, bottom) across the middle half of a side
    counts when some pixel within RING_BAND of the edge changes at FOCUS_RATIO; a
    rounded ring (browser Nova's pill URL bar) curves through the band."""
    x0, y0, w, h = box['x'], box['y'], box['width'], box['height']
    rows = range(y0 + h // 4, y0 + h - h // 4)
    columns = range(x0 + w // 4, x0 + w - w // 4)
    band = range(min(RING_BAND, w, h))
    crossings = {
        'left': [[(x0 + d, y) for d in band] for y in rows],
        'right': [[(x0 + w - 1 - d, y) for d in band] for y in rows],
        'top': [[(x, y0 + d) for d in band] for x in columns],
        'bottom': [[(x, y0 + h - 1 - d) for d in band] for x in columns],
    }
    strong = lambda x, y: ratio(after.pixel(x, y), before.pixel(x, y)) >= FOCUS_RATIO
    return {side: bool(lines) and sum(any(strong(x, y) for x, y in line) for line in lines) >= RING_MATCH * len(lines)
            for side, lines in crossings.items()}


def judge_focus(name, kind, before_path, after_path, rect, mask_path):
    """Return (record, failures); raise when the change is not at the target."""
    before, after = Image(before_path), Image(after_path)
    total = count(changed_mask(before_path, after_path, mask_path))
    target = box_of(rect)
    region = inflate(target, PAD)
    changed = [(x, y) for x, y in before.coordinates(region) if before.pixel(x, y) != after.pixel(x, y)]
    record = {'surface': name, 'kind': kind, 'target': rect, 'changedNear': len(changed), 'changedTotal': total}
    if not changed:
        return record, [f'{name} paints no keyboard focus indicator']
    if len(changed) < NEAR_TARGET * total:
        raise RuntimeError(f'{name}: only {len(changed)} of {total} changed pixels lie near {rect}; '
                           'wrong target measured')
    if kind == 'ring':
        box = bounds(changed)
        sides = ring_sides(before, after, box)
        record.update(box=box, sides=sides)
        # A ring surrounds its target (Firefox rings a toolbar button's icon, inset in the
        # button); a change inside it, such as a text selection, is not a ring.
        cx, cy = target['x'] + target['width'] / 2, target['y'] + target['height'] / 2
        encloses = (box['x'] <= cx <= box['x'] + box['width'] and box['y'] <= cy <= box['y'] + box['height']
                    and box['width'] >= RING_SPAN * target['width']
                    and box['height'] >= RING_SPAN * target['height'])
        if not encloses:
            return record, [f'{name} focus change {box} does not enclose its target {target}']
        missing = [side for side, ok in sides.items() if not ok]
        if missing:
            return record, [f'{name} focus ring missing or below {FOCUS_RATIO}:1 on {", ".join(missing)}']
        return record, []
    distinct = sum(1 for x, y in before.coordinates(target)
                   if not near(before.pixel(x, y), after.pixel(x, y), DISTINCT))
    area = target['width'] * target['height']
    record['activeShare'] = round(distinct / area, 3) if area else 0
    if not area or distinct < FILL_MATCH * area:
        return record, [f'{name} active background covers {record["activeShare"]} of the row, '
                        f'less than {FILL_MATCH}']
    return record, []


def keys(m, key):
    m.execute_script(KEY_JS, script_args=[list(key)], sandbox='system')


def focus_checks(m, capture):
    records, failures = [], {}
    m.execute_script("gBrowser.selectedBrowser.focus()", sandbox='system')
    time.sleep(.3)
    unfocused = capture.shoot('', False)
    if count(changed_mask(unfocused, capture.shoot('', False), capture.directory / 'focus-control.png')):
        raise RuntimeError('focus: captures unstable')
    for name, (how, action), matches, target, kind in FOCUS_SURFACES:
        for _ in range(16):
            if how == 'keys':
                keys(m, action)
            else:
                m.execute_script(action, sandbox='system')
            time.sleep(.15)
            if m.execute_script(ACTIVE_JS, script_args=[matches], sandbox='system'):
                break
        else:
            raise RuntimeError(f'{name}: keyboard focus never reached {matches}')
        rect = m.execute_script(RECT_JS, script_args=[target], sandbox='system')
        focused = capture.shoot('', False)
        record, failures[name] = judge_focus(name, kind, unfocused, focused, rect,
                                             capture.directory / f'focus-{name}.png')
        records.append(record)
    m.execute_script("gBrowser.selectedBrowser.focus()", sandbox='system')
    for name, opener, is_open, target, kind, closer in POPUP_FOCUS:
        m.execute_script(opener, sandbox='system')
        wait(m, is_open, f'{name} popup open')
        time.sleep(1)
        before = capture.shoot('', False)
        for _ in range(8):
            keys(m, DOWN)
            time.sleep(.15)
            if m.execute_script("return !!document.querySelector(arguments[0]) && "
                                "(document.querySelector(arguments[0]).matches(':focus-visible') || "
                                "document.querySelector(arguments[0]).hasAttribute('_moz-menuactive'))",
                                script_args=[target], sandbox='system'):
                break
        else:
            raise RuntimeError(f'{name}: keyboard never reached {target}')
        rect = m.execute_script(RECT_JS, script_args=[target], sandbox='system')
        after = capture.shoot('', False)
        record, failures[name] = judge_focus(name, kind, before, after, rect,
                                             capture.directory / f'focus-{name}.png')
        records.append(record)
        m.execute_script(closer, sandbox='system')
        time.sleep(.5)
    return records, failures


def motion_checks(m, capture, env):
    records, failures = [], {}
    # No freeze sheet here: transitions must run as shipped.
    m.execute_async_script(CHROME_STYLE_JS, script_args=['caret-color: auto;'], sandbox='system')
    idle = m.execute_script(ANIMATIONS_JS, sandbox='system')
    if idle:  # reduced motion: nothing in chrome may animate at rest
        raise RuntimeError(f'motion: animations running at idle under reduced motion: {idle}')
    for name, selector in MOTION_SURFACES:
        colors = resolve(m, name, selector)
        rect = m.execute_script(RECT_JS, script_args=[selector], sandbox='system')
        if not colors or not rect:
            raise RuntimeError(f'motion {name}: {selector} missing')
        box = box_of(rect)
        accent, border = colors['--tf-accent'], colors['--tf-border']
        if near(accent, border, DISTINCT):
            raise RuntimeError(f'motion {name}: hover accent {accent} indistinguishable from border {border}')
        resting = Image(shoot_plain(capture, env, f'motion-{name}-rest'))
        if not all(border_sides(resting, box, border).values()):
            raise RuntimeError(f'motion {name}: resting --tf-border not painted at {box}; wrong target')
        started = m.execute_async_script(HOVER_JS, script_args=[selector, True], sandbox='system')
        first = Image(shoot_plain(capture, env, f'motion-{name}-hover', settle=.25))
        sides = border_sides(first, box, accent)
        m.execute_async_script(HOVER_JS, script_args=[selector, False], sandbox='system')
        record = {'surface': name, 'box': box, 'accent': accent, 'started': started, 'sides': sides}
        records.append(record)
        problems = []
        if started:
            problems.append(f'motion {name}: hover starts {started} under reduced motion')
        missing = [side for side, ok in sides.items() if not ok]
        if missing:
            problems.append(f'motion {name}: first hovered frame lacks settled --tf-accent {accent} on '
                            + ', '.join(missing))
        failures[name] = problems
        time.sleep(.3)
    records.append({'idleAnimations': idle})
    return records, failures



def shoot_plain(capture, env, label, settle=.5):
    time.sleep(settle)
    capture.shots += 1
    path = capture.directory / f'shot-{capture.shots}-{label}.ppm'
    subprocess.run(['grim', '-t', 'ppm', str(path)], env=env, check=True, stdout=subprocess.DEVNULL)
    return path


def rtl_checks(m, capture):
    records, failures = [], {}
    openers = []
    for name, selector, border_var, text_selector, opener in CHROME_SURFACES:
        if opener:
            m.execute_script(opener[0], sandbox='system')
            wait(m, opener[1], f'{name} open')
            time.sleep(1)
            openers.append(name)
        colors = resolve(m, name, selector)
        if not colors:
            raise RuntimeError(f'rtl {name}: {selector} missing')
        shot = capture.surface(name, selector, text_selector)
        record, failures[name] = judge(name, shot, colors, border_var=border_var)
        records.append(record)
    m.execute_script("document.getElementById('toolbar-context-menu').hidePopup(); gURLBar.view.close(); "
                     "gBrowser.selectedBrowser.focus()", sandbox='system')
    time.sleep(.5)
    # Mirroring from painted boxes: what comes later in DOM order paints further left.
    m.execute_script("gBrowser.tabs[0].setAttribute('data-tf-mirror', 'first'); "
                     "gBrowser.tabs[1].setAttribute('data-tf-mirror', 'second')", sandbox='system')
    for name, later, earlier in (('mirrored-navbar', '#PanelUI-menu-button', '#urlbar'),
                                 ('mirrored-tabs', '[data-tf-mirror="second"]', '[data-tf-mirror="first"]')):
        left = capture.surface(f'{name}-later', later)['box']
        right = capture.surface(f'{name}-earlier', earlier)['box']
        records.append({'surface': name, 'later': left, 'earlier': right})
        if not left or not right:
            failures[name] = [f'rtl {name}: {later} {left} or {earlier} {right} paints nothing']
        elif left['x'] + left['width'] > right['x']:
            failures[name] = [f'rtl {name}: {later} paints at {left}, not mirrored left of {earlier} at {right}']
        else:
            failures[name] = []
    return records, failures


def run(env, nova, locale, config, kind, checks, xpi):
    """Launch Firefox and return (metadata, records, {check: {surface: failures}})."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-a11y-') as name:
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
            'ui.prefersReducedMotion': 1,
            'marionette.port': port,
        }
        if locale == 'he':
            (profile / 'extensions').mkdir()
            shutil.copy2(xpi, profile / 'extensions' / f'{LANGPACK_ID}.xpi')
            prefs.update({'intl.locale.requested': 'he', 'extensions.autoDisableScopes': 0})
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        shots = directory
        if os.environ.get('TF_SURFACE_ARTIFACTS'):
            shots = Path(os.environ['TF_SURFACE_ARTIFACTS']) / f'a11y-nova-{str(nova).lower()}-{locale}-{kind}'
            shots.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env={**env, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta_js = ("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, "
                       "version:Services.appinfo.version, buildID:Services.appinfo.appBuildID, "
                       "wayland:Services.env.get('WAYLAND_DISPLAY'), "
                       "headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless, "
                       "dpr:devicePixelRatio, browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                       "locale:Services.locale.appLocaleAsBCP47, dir:getComputedStyle(document.documentElement).direction, "
                       "reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches}")
            deadline = time.monotonic() + 20
            while True:
                meta = m.execute_script(meta_js, sandbox='system')
                if meta['locale'].split('-')[0] == locale or time.monotonic() >= deadline:
                    break
                time.sleep(.25)
            expected_dir = 'rtl' if locale == 'he' else 'ltr'
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['headless'] or not meta['wayland'] or meta['browserNova'] != nova or meta['dpr'] != 1
                    or meta['locale'].split('-')[0] != locale or meta['dir'] != expected_dir or not meta['reducedMotion']):
                raise RuntimeError(f'unexpected Firefox process, profile, display, locale or preference: {meta}')
            m.execute_script("document.documentElement.removeAttribute('remotecontrol')", sandbox='system')
            capture = StableCapture(m, shots, env)
            m.execute_script("gBrowser.addTrustedTab('about:blank'); gURLBar.value = 'textfox paint probe'; "
                             "gBrowser.getFindBar().then(f => { f.open(); f._findField.value = 'textfox'; })",
                             sandbox='system')
            wait(m, "return gBrowser.tabs.length == 2 && !!document.querySelector('findbar:not([hidden])')",
                 'second tab and findbar')
            time.sleep(1)
            records, found = [], {}
            for check in checks:
                if check == 'rtl':
                    rec, found[check] = rtl_checks(m, capture)
                elif check == 'focus':
                    rec, found[check] = focus_checks(m, capture)
                else:
                    rec, found[check] = motion_checks(m, capture, env)
                records.append({'check': check, 'records': rec})
            return meta, records, found
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                stop(process, 15)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', type=Path, help='config.css to check in place of the sentinel config')
    parser.add_argument('--only', choices=['focus', 'motion', 'rtl'], help='run one check (debugging)')
    parser.add_argument('--nova', choices=['false', 'true'], help='run one browser Nova state (debugging)')
    parser.add_argument('--locale', choices=list(CHECKS_BY_LOCALE), help='run one locale (debugging)')
    args = parser.parse_args()
    good = dict(GOOD)
    if args.config:
        del good['sentinel']
        good['given'] = args.config.read_text()
    version = subprocess.check_output(['firefox', '--version'], text=True).split()[-1]
    xpi = langpack_for(version)
    failures, measured = [], {}
    with tempfile.TemporaryDirectory(prefix='tf-a11y-sway-') as name:
        directory = Path(name)
        directory.chmod(0o700)
        env = os.environ.copy()
        sway = start_sway(directory, env)
        try:
            for nova in [n for n in (False, True) if not args.nova or str(n).lower() == args.nova]:
                for locale, checks in CHECKS_BY_LOCALE.items():
                    if args.locale and locale != args.locale:
                        continue
                    checks = [c for c in checks if not args.only or c == args.only]
                    cases = [(k, c, checks) for k, c in good.items()]
                    cases += [(f'known-bad-{name}', bad[1], [bad[0]]) for name, bad in KNOWN_BAD.items()
                              if bad[0] in checks]
                    for kind, config, run_checks in cases:
                        if not run_checks:
                            continue
                        meta, records, found = run(env, nova, locale, config, kind, run_checks, xpi)
                        print(json.dumps({'nova': nova, 'locale': locale, 'config': kind, 'firefox': meta,
                                          'checks': records}), flush=True)
                        label = f'nova={nova} locale={locale} config={kind}'
                        for check, surfaces in found.items():
                            key = (nova, locale, check)
                            if measured.setdefault(key, list(surfaces)) != list(surfaces):
                                raise RuntimeError(f'{label}: {check} surfaces differ: {list(surfaces)}')
                            if kind.startswith('known-bad'):
                                _, _, applies, signs = KNOWN_BAD[kind.removeprefix('known-bad-')]
                                undetected = [s for s, problems in surfaces.items() if applies(s) and
                                              not any(sign in p for p in problems for sign in signs)]
                                if undetected:
                                    raise RuntimeError(f'{label}: known-bad config not detected by {check} on '
                                                       + ', '.join(undetected))
                                continue
                            failures += [f'{label} {check}: {p}' for problems in surfaces.values() for p in problems]
        finally:
            stop(sway, 10)
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        return 1
    summary = '; '.join(f'nova={n} {loc} {c}: {len(s)}' for (n, loc, c), s in measured.items())
    print(f'PASS: focus, motion and RTL paint correctly ({", ".join(good)}); '
          f'each known-bad config fails every surface of its check. Surfaces: {summary}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BaseException as error:  # an untrustworthy run is a failure, never inconclusive
        if isinstance(error, SystemExit) and error.code in (0, 1):
            raise
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
