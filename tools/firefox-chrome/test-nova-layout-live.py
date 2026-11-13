#!/usr/bin/env python3
"""Check the Nova new-tab layout from headed, painted pixels; exit 0 pass, 1 fail.

Starts a disposable 1280x800 headless Sway compositor, runs Firefox headed in
it, and captures the compositor output with grim. Visibility and overlap come
from painted pixels, not estimates: the generated logo and the search box are
each hidden in turn (layout kept), and the pixels that change are the pixels
that element painted. The logo passes only if it paints, the search box paints,
and no logo pixel lands inside the search box's painted region. Before the
verdict, an injected overlap fixture must be detected and repeated captures
must match, or the run fails.

Requires sway, grim, ImageMagick, and the live-tool venv Python. The disposable
profile enables new-tab feeds and may make network requests.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = (1280, 800)
# Least logo pixels that count as a visible logo; the default logo paints ~1400.
MIN_LOGO_PIXELS = 100

FREEZE = ('*, *::before, *::after { animation: none !important; transition: none !important; '
          'caret-color: transparent !important; }'
          # Live widgets repaint between captures: the world clock ticks each minute, and
          # weather, crossword and wallpaper arrive from the network. Hidden, layout kept.
          '.nova-enabled :is(.sidebar-inline-end, .content-full-width), '
          '.nova-enabled :is(.sidebar-inline-end, .content-full-width) * '
          '{ visibility: hidden !important; }')
HIDE_LOGO = '.nova-enabled .wordmark::before { visibility: hidden !important; }'
HIDE_SEARCH = '.nova-enabled .search-wrapper { visibility: hidden !important; }'
# Fixed square at content (10, 10); its painted box locates the content area on screen,
# because Firefox cannot report its own window position under Wayland.
MARKER = (10, 20)
SHOW_MARKER = (f'body::after {{ content: ""; position: fixed; z-index: 2147483647; '
               f'left: {MARKER[0]}px; top: {MARKER[0]}px; width: {MARKER[1]}px; height: {MARKER[1]}px; '
               'background: CanvasText; outline: 2px solid Canvas; }')

GEOMETRY_JS = r"""
const q = s => document.querySelector(s);
const wordmark = q('.nova-enabled .wordmark');
const wrapper = q('.nova-enabled .search-wrapper');
const inner = q('.nova-enabled .search-inner-wrapper');
if (!wordmark || !wrapper || !inner) throw new Error('Nova logo or search elements missing');
const box = e => { const r = e.getBoundingClientRect();
  return {x:r.x + arguments[0], y:r.y + arguments[1], width:r.width, height:r.height}; };
return {dpr:devicePixelRatio, viewport:[innerWidth, innerHeight], contentOrigin:[arguments[0], arguments[1]],
  wordmarkDisplay:getComputedStyle(wordmark).display,
  logoContent:getComputedStyle(wordmark, '::before').content,
  wordmark:box(wordmark), searchWrapper:box(wrapper), searchInner:box(inner)};
"""

SHORTCUT_JS = r"""
const title = document.querySelector('.nova-enabled .top-site-outer .title');
if (!title) throw new Error('Nova shortcut title missing');
const label = title.querySelector('span') || title;
label.textContent = 'Wikipedia';
const range = document.createRange();
const measureText = element => {
  range.selectNodeContents(element);
  const lines = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0)
    .map(r => ({x:r.x, y:r.y, width:r.width, height:r.height}));
  const clips = [{name:'viewport', left:0, top:0, right:innerWidth, bottom:innerHeight}];
  for (let e = element; e; e = e.parentElement) {
    const style = getComputedStyle(e), r = e.getBoundingClientRect();
    const clipX = /^(hidden|clip|scroll|auto)$/.test(style.overflowX);
    const clipY = /^(hidden|clip|scroll|auto)$/.test(style.overflowY);
    if (clipX || clipY) clips.push({name:e.localName + '.' + e.className,
      left:clipX ? r.left + e.clientLeft : -Infinity,
      right:clipX ? r.left + e.clientLeft + e.clientWidth : Infinity,
      top:clipY ? r.top + e.clientTop : -Infinity,
      bottom:clipY ? r.top + e.clientTop + e.clientHeight : Infinity});
  }
  const clipped = [];
  for (const line of lines) for (const clip of clips) {
    if (line.x < clip.left - 1 || line.x + line.width > clip.right + 1 ||
        line.y < clip.top - 1 || line.y + line.height > clip.bottom + 1)
      clipped.push(clip.name);
  }
  return {lines, clipped:[...new Set(clipped)]};
};
const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x &&
  a.y < b.y + b.height && a.y + a.height > b.y;
const shortcut = measureText(label);
const neighbors = [...document.querySelectorAll('.nova-enabled .top-site-outer .title .title-label')]
  .filter(e => e !== label && e.getClientRects().length)
  .flatMap(e => measureText(e).lines);
shortcut.neighborLinesMeasured = neighbors.length;
shortcut.neighborCollisions = shortcut.lines.filter(line => neighbors.some(other => overlaps(line, other))).length;
const fixture = document.createElement('div');
Object.assign(fixture.style, {position:'fixed', left:'0', top:'0', width:'10px',
  height:'20px', overflow:'hidden', whiteSpace:'nowrap'});
fixture.textContent = 'A deliberately clipped label';
document.body.appendChild(fixture);
const clippingSelfTest = measureText(fixture);
fixture.style.overflow = 'visible';
const collisionFixture = fixture.cloneNode(true);
collisionFixture.textContent = 'Collision';
document.body.appendChild(collisionFixture);
const collisionSelfTest = measureText(fixture).lines.some(a =>
  measureText(collisionFixture).lines.some(b => overlaps(a, b)));
fixture.remove();
collisionFixture.remove();
return {fixture:'Wikipedia', ...shortcut, clippingSelfTest, collisionSelfTest};
"""

SET_STYLE_JS = r"""
let style = document.getElementById('tf-layout-test');
if (!style) { style = document.createElement('style'); style.id = 'tf-layout-test'; document.head.appendChild(style); }
style.textContent = arguments[0];
document.activeElement?.blur?.();
const done = arguments[arguments.length - 1];
requestAnimationFrame(() => requestAnimationFrame(() => done(true)));
"""


def magick(*args):
    return subprocess.check_output(['magick', *map(str, args)], text=True).strip()


def changed_mask(first, second, out):
    magick(first, second, '-compose', 'difference', '-composite', '-colorspace', 'gray',
           '-threshold', '0', out)
    return out


def count(mask, crop=None):
    cropping = ['-crop', crop, '+repage'] if crop else []
    return round(float(magick(mask, *cropping, '-format', '%[fx:mean*w*h]', 'info:')))


def painted_box(mask):
    """Bounding box of changed (white) pixels, or None when nothing changed."""
    if not count(mask):
        return None
    w, h, x, y = (int(v) for v in magick(mask, '-format', '%@', 'info:')
                  .replace('x', ' ').replace('+', ' ').split())
    return {'x': x, 'y': y, 'width': w, 'height': h}


def crop_of(box):
    return f"{box['width']}x{box['height']}+{box['x']}+{box['y']}"


def centered_in(inner, outer):
    cx, cy = inner['x'] + inner['width'] / 2, inner['y'] + inner['height'] / 2
    return (outer['x'] <= cx <= outer['x'] + outer['width']
            and outer['y'] <= cy <= outer['y'] + outer['height'])


def union(a, b):
    x, y = int(min(a['x'], b['x'])), int(min(a['y'], b['y']))
    right = int(max(a['x'] + a['width'], b['x'] + b['width']) + .999)
    bottom = int(max(a['y'] + a['height'], b['y'] + b['height']) + .999)
    return {'x': max(0, x), 'y': max(0, y),
            'width': min(OUTPUT[0], right) - max(0, x), 'height': min(OUTPUT[1], bottom) - max(0, y)}


class Page:
    def __init__(self, m, directory, env):
        self.m, self.directory, self.env, self.shots = m, directory, env, 0

    def capture(self, css):
        self.m.execute_async_script(SET_STYLE_JS, script_args=[FREEZE + css])
        time.sleep(.5)  # let the compositor commit the frame grim reads
        self.shots += 1
        path = self.directory / f'shot-{self.shots}.png'
        subprocess.run(['grim', str(path)], env=self.env, check=True, stdout=subprocess.DEVNULL)
        size = [int(v) for v in magick('identify', '-format', '%w %h', path).split()]
        if size != list(OUTPUT):
            raise RuntimeError(f'unexpected compositor capture size {size}')
        return path

    def content_origin(self):
        """Screen position of the content viewport, measured from a painted marker."""
        base = self.capture('')
        marked = self.capture(SHOW_MARKER)
        box = painted_box(changed_mask(base, marked, self.directory / 'mask-marker.png'))
        size = MARKER[1] + 4  # square plus its outline on every side
        if not box or abs(box['width'] - size) > 1 or abs(box['height'] - size) > 1:
            raise RuntimeError(f'position marker painted {box}, expected about {size}px square')
        offset = MARKER[0] - 2
        return box['x'] - offset, box['y'] - offset

    def measure(self, search_rect, extra_css=''):
        """Paint the logo and search box, each hidden in turn, and diff them.

        The search region is the union of the search box's painted pixels and its
        DOM box, so a logo pixel anywhere in either counts as overlap."""
        base = self.capture(extra_css)
        control = self.capture(extra_css)
        no_logo = self.capture(extra_css + HIDE_LOGO)
        no_search = self.capture(extra_css + HIDE_SEARCH)
        restored = self.capture(extra_css)
        stem = self.directory / f'mask-{self.shots}'
        logo = changed_mask(base, no_logo, f'{stem}-logo.png')
        search = changed_mask(base, no_search, f'{stem}-search.png')
        search_box = painted_box(search)
        region = union(search_box, search_rect) if search_box else None
        return {'controlChangedPixels': count(changed_mask(base, control, f'{stem}-control.png')),
                'restoredChangedPixels': count(changed_mask(base, restored, f'{stem}-restored.png')),
                'logoPixels': count(logo), 'logoBox': painted_box(logo),
                'searchPixels': count(search), 'searchBox': search_box,
                'searchRegion': region,
                'logoPixelsInSearch': count(logo, crop_of(region)) if region else None}


def start_sway(directory, env):
    config = directory / 'sway.conf'
    config.write_text(f'output * mode {OUTPUT[0]}x{OUTPUT[1]} scale 1\n'
                      'default_border none\ndefault_floating_border none\n'
                      'seat seat0 fallback true\n')
    env.update({'XDG_RUNTIME_DIR': str(directory), 'WLR_BACKENDS': 'headless',
                'WLR_RENDERER': 'pixman', 'WLR_LIBINPUT_NO_DEVICES': '1', 'MOZ_ENABLE_WAYLAND': '1'})
    env.pop('MOZ_HEADLESS', None)
    env.pop('DISPLAY', None)
    sway = subprocess.Popen(['sway', '--unsupported-gpu', '-c', str(config)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while True:
        sockets = [p for p in directory.glob('wayland-*') if p.is_socket()]
        if sockets:
            env['WAYLAND_DISPLAY'] = sockets[0].name
            return sway
        if sway.poll() is not None or time.monotonic() >= deadline:
            sway.kill()
            raise RuntimeError('headless Sway did not start')
        time.sleep(.1)


def stop(process, timeout):
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def verdict(geometry, real, fixture, shortcut):
    """Return failures; raise when the instrument itself cannot be trusted."""
    for name, run in (('real', real), ('fixture', fixture)):
        if run['controlChangedPixels'] or run['restoredChangedPixels']:
            raise RuntimeError(f'{name} captures unstable: {run}')
    if not fixture['logoPixelsInSearch']:
        raise RuntimeError(f'injected logo/search overlap not detected: {fixture}')
    if not shortcut['clippingSelfTest']['lines'] or not shortcut['clippingSelfTest']['clipped']:
        raise RuntimeError('clipping check failed its deliberately clipped fixture')
    if not shortcut['collisionSelfTest']:
        raise RuntimeError('collision check failed its overlapping fixture')
    failures = []
    if geometry['wordmarkDisplay'] == 'none' or geometry['logoContent'] in ('none', 'normal'):
        failures.append('logo not rendered at this viewport')
    if real['logoPixels'] < MIN_LOGO_PIXELS:
        failures.append(f"logo paints {real['logoPixels']} pixels, fewer than {MIN_LOGO_PIXELS}")
    if not real['searchBox']:
        failures.append('search box paints no pixels')
    elif not centered_in(real['searchBox'], geometry['searchWrapper']):
        raise RuntimeError(f"search paint {real['searchBox']} outside its DOM box "
                           f"{geometry['searchWrapper']}; screen mapping is wrong")
    elif real['logoPixelsInSearch']:
        failures.append(f"{real['logoPixelsInSearch']} logo pixels paint inside the search box")
    if not shortcut['lines']:
        failures.append('Wikipedia shortcut has no measurable text')
    if shortcut['clipped']:
        failures.append('Wikipedia shortcut text clipped by ' + ', '.join(shortcut['clipped']))
    if not shortcut['neighborLinesMeasured']:
        failures.append('no neighboring shortcut text measured, so collision is unchecked')
    if shortcut['neighborCollisions']:
        failures.append('Wikipedia shortcut text collides with another title')
    return failures


def run(directory, env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    profile = directory / 'profile'
    profile.mkdir()
    shutil.copytree(ROOT / 'chrome', profile / 'chrome')
    prefs = {
        'toolkit.legacyUserProfileCustomizations.stylesheets': True,
        'browser.nova.enabled': True,
        'browser.newtabpage.activity-stream.nova.enabled': True,
        'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
        'browser.newtabpage.activity-stream.widgets.enabled': True,
        'browser.shell.checkDefaultBrowser': False,
        'marionette.port': port,
    }
    (profile / 'user.js').write_text(''.join(
        f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
    env = dict(env, MOZ_REMOTE_ALLOW_SYSTEM_ACCESS='1')
    process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                '--new-instance', '--profile', str(profile), 'about:blank'],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    m = None
    try:
        m = connect(port)
        m.set_context('chrome')
        metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, buildID:Services.appinfo.appBuildID, widget:AppConstants.MOZ_WIDGET_TOOLKIT, wayland:Services.env.get('WAYLAND_DISPLAY'), headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless, browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')}", sandbox='system')
        if process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve():
            raise RuntimeError('Marionette connected to an unexpected Firefox profile')
        if metadata['headless'] or not metadata['browserNova'] or not metadata['newtabNova']:
            raise RuntimeError(f'Firefox is headless or Nova is off: {metadata}')
        m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
        m.set_context('content')
        m.switch_to_window(m.window_handles[-1])
        deadline = time.monotonic() + 30
        while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .top-site-outer .title")'):
            if time.monotonic() >= deadline:
                raise RuntimeError('Nova new-tab shortcut did not initialize')
            time.sleep(.25)
        time.sleep(2)
        shortcut = m.execute_script(SHORTCUT_JS)
        page = Page(m, directory, env)
        geometry = m.execute_script(GEOMETRY_JS, script_args=list(page.content_origin()))
        if geometry['dpr'] != 1:
            raise RuntimeError(f'unsupported device pixel ratio: {geometry}')
        real = page.measure(geometry['searchWrapper'])
        # Known-bad fixture: move the logo onto the search box, painted above it.
        dx = geometry['searchInner']['x'] - geometry['wordmark']['x']
        dy = geometry['searchInner']['y'] - geometry['wordmark']['y']
        fixture = page.measure(geometry['searchWrapper'], f'.nova-enabled .wordmark {{ position: relative !important; z-index: 10 !important; '
                               f'transform: translate({dx}px, {dy}px) !important; }}')
        result = {'firefox': metadata, 'geometry': geometry, 'real': real,
                  'overlapFixture': fixture, 'shortcut': shortcut}
        print(json.dumps(result, indent=2), flush=True)
        if len({round(r['y'], 1) for r in shortcut['lines']}) > 1:
            print('OBSERVATION: Wikipedia shortcut wraps; wrapping alone is not a layout failure',
                  file=sys.stderr)
        return verdict(geometry, real, fixture, shortcut)
    finally:
        try:
            if m:
                m.delete_session()
        finally:
            stop(process, 15)


def main():
    with tempfile.TemporaryDirectory(prefix='tf-nova-layout-') as name:
        directory = Path(name)
        directory.chmod(0o700)
        env = os.environ.copy()
        sway = start_sway(directory, env)
        try:
            failures = run(directory, env)
        finally:
            stop(sway, 10)
    if failures:
        print('FAIL: ' + '; '.join(failures), file=sys.stderr)
        return 1
    print('PASS: logo and search box paint visibly with no logo pixel inside the search box')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:  # an untrustworthy run is a failure, never inconclusive
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
