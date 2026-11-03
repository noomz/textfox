#!/usr/bin/env python3
"""Compare new-tab search pixels with the generated logo visible and transparent.

Requires ImageMagick. Uses a disposable profile whose new-tab feed may use network.
The result is observational, not a visual-accessibility or clipping test.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def difference(first, second, crop):
    command = ['magick', str(first), str(second), '-crop', crop, '+repage',
               '-compose', 'difference', '-composite', '-format', '%[fx:mean]', 'info:']
    return float(subprocess.check_output(command, text=True))


def changed_pixels(first, second, crop):
    command = ['magick', str(first), str(second), '-crop', crop, '+repage',
               '-compose', 'difference', '-composite', '-colorspace', 'gray',
               '-threshold', '0', '-format', '%[fx:mean*w*h]', 'info:']
    return round(float(subprocess.check_output(command, text=True)))


def viewport_crop(rect, viewport):
    left, top = max(0, int(rect['x'])), max(0, int(rect['y']))
    right = min(viewport['viewportWidth'], int(rect['x'] + rect['width']))
    bottom = min(viewport['viewportHeight'], int(rect['y'] + rect['height']))
    if right <= left or bottom <= top:
        raise RuntimeError(f'region outside viewport: {rect}')
    return f'{right - left}x{bottom - top}+{left}+{top}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--rtl', action='store_true')
    parser.add_argument('--font-size', type=int, default=14)
    parser.add_argument('--long-logo', action='store_true')
    parser.add_argument('--reduced-motion', action='store_true')
    parser.add_argument('--forced-colors', action='store_true')
    parser.add_argument('--experiment', action='store_true')
    parser.add_argument('--experiment-wrap', action='store_true')
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-logo-paint-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        logo = f' --tf-newtab-logo: {json.dumps("W" * 100)};' if args.long_logo else ''
        (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-font-size: {args.font_size}px;{logo} }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': True,
                 'browser.newtabpage.activity-stream.nova.enabled': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'intl.l10n.pseudo': 'bidi' if args.rtl else '',
                 'ui.prefersReducedMotion': 1 if args.reduced_motion else 0,
                 'browser.display.document_color_use': 2 if args.forced_colors else 1}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_window_rect(width=args.width, height=800)
            m.set_context('chrome')
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')}", sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or not metadata['browserNova'] or not metadata['newtabNova']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .wordmark") && !!document.querySelector(".nova-enabled .search-wrapper")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Nova new-tab did not initialize')
                time.sleep(.25)
            time.sleep(2)
            if args.experiment or args.experiment_wrap:
                m.execute_script("""
                    const style = document.createElement('style');
                    style.textContent = `.container.nova-enabled { grid-template-rows: ${arguments[0] ? 'minmax(var(--row-height), auto)' : 'auto'} var(--row-height) !important; } .nova-enabled .sidebar-inline-start { grid-row: 1 !important; } .nova-enabled .wordmark { height: auto !important; }`;
                    if (arguments[0]) style.textContent += '.nova-enabled .wordmark { max-inline-size: calc(100cqi - var(--side-col-width) - 3 * var(--space-xlarge)) !important; } .nova-enabled .wordmark::before { overflow-wrap: anywhere !important; }';
                    document.head.appendChild(style);
                    """, script_args=[args.experiment_wrap])
            geometry = m.execute_script("""
                const r = document.querySelector('.nova-enabled .search-inner-wrapper').getBoundingClientRect();
                const logo = document.querySelector('.nova-enabled .wordmark');
                const ps = getComputedStyle(logo, '::before');
                const end = document.querySelector('.container.nova-enabled > .sidebar-inline-end').getBoundingClientRect();
                const start = document.querySelector('.container.nova-enabled > .sidebar-inline-start').getBoundingClientRect();
                const parents = [];
                for (let e = logo; e && parents.length < 5; e = e.parentElement) {
                  const s = getComputedStyle(e), b = e.getBoundingClientRect();
                  parents.push({tag:e.localName, class:e.className, display:s.display,
                    gridColumn:s.gridColumn, gridRow:s.gridRow, gridTemplateColumns:s.gridTemplateColumns,
                    gridTemplateRows:s.gridTemplateRows, x:b.x, y:b.y, width:b.width, height:b.height});
                }
                return {x:r.x, y:r.y, width:r.width, height:r.height,
                        viewportWidth:innerWidth, viewportHeight:innerHeight, dpr:devicePixelRatio,
                        direction:getComputedStyle(document.documentElement).direction,
                        reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,
                        forcedColors:matchMedia('(forced-colors: active)').matches,
                        endSidebar:{x:end.x, y:end.y, width:end.width, height:end.height},
                        startSidebar:{x:start.x, y:start.y, width:start.width, height:start.height},
                        logoDisplay:getComputedStyle(logo).display, pseudoContent:ps.content, parents,
                        sideControls:[...document.querySelectorAll('.container.nova-enabled > aside :is(button, a, input, moz-button)')].filter(e => e.getClientRects().length).map(e => {
                          const b=e.getBoundingClientRect(); return {tag:e.localName, class:e.className,
                            x:b.x, y:b.y, width:b.width, height:b.height};
                        }),
                        gridChildren:[...document.querySelector('.container.nova-enabled').children].map(e => {
                          const s = getComputedStyle(e), b = e.getBoundingClientRect();
                          return {tag:e.localName, class:e.className, display:s.display, gridRow:s.gridRow,
                            gridColumn:s.gridColumn, x:b.x, y:b.y, width:b.width, height:b.height};
                        })};
                """)
            if geometry['direction'] != ('rtl' if args.rtl else 'ltr'):
                raise RuntimeError(f'unexpected document direction: {geometry}')
            if geometry['reducedMotion'] != args.reduced_motion:
                raise RuntimeError(f'unexpected reduced-motion state: {geometry}')
            if geometry['forcedColors'] != args.forced_colors:
                raise RuntimeError(f'unexpected content forced-colors state: {geometry}')
            # Firefox screenshots use CSS pixels; reject unexpected image dimensions below.
            first, control, changed = (profile / name for name in ('first.png', 'control.png', 'changed.png'))
            first.write_bytes(m.screenshot(format='binary', full=False))
            control.write_bytes(m.screenshot(format='binary', full=False))
            m.execute_script("""
                const style = document.createElement('style');
                style.textContent = '.nova-enabled .logo-and-wordmark .wordmark::before { color: transparent !important; }';
                document.head.appendChild(style);
                """)
            changed.write_bytes(m.screenshot(format='binary', full=False))
            after = m.execute_script("""
                const r = document.querySelector('.nova-enabled .search-inner-wrapper').getBoundingClientRect();
                return {x:r.x, y:r.y, width:r.width, height:r.height};
                """)
            if any(after[key] != geometry[key] for key in ('x', 'y', 'width', 'height')):
                raise RuntimeError(f'search geometry changed during intervention: {geometry}, {after}')
            size = subprocess.check_output(['magick', 'identify', '-format', '%w %h', str(first)], text=True).split()
            if [int(v) for v in size] != [geometry['viewportWidth'], geometry['viewportHeight']]:
                raise RuntimeError(f'screenshot dimensions do not match CSS viewport: {size}, {geometry}')
            x, y = int(geometry['x']), int(geometry['y'])
            width, height = int(geometry['width']), int(geometry['height'])
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise RuntimeError(f'invalid search crop: {geometry}')
            crop = f'{width}x{height}+{x}+{y}'
            full_crop = f"{geometry['viewportWidth']}x{geometry['viewportHeight']}+0+0"
            full_difference = difference(control, changed, full_crop)
            end_crop = viewport_crop(geometry['endSidebar'], geometry)
            start_crop = viewport_crop(geometry['startSidebar'], geometry)
            m.find_element('css selector', '.nova-enabled content-search-handoff-ui').click()
            m.set_context('chrome')
            deadline = time.monotonic() + 5
            while not m.execute_script('return gURLBar.focused', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('search click did not focus URL bar')
                time.sleep(.1)
            print(json.dumps({'firefox': metadata, 'requestedWidth': args.width, 'fontSize': args.font_size,
                              'longLogo': args.long_logo, 'searchClickFocusedUrlbar': True,
                              'experiment': args.experiment, 'experimentWrap': args.experiment_wrap,
                              'interventionVisible': full_difference != 0,
                              'search': geometry, 'crop': crop,
                              'controlDifference': difference(first, control, crop),
                              'logoTransparentDifference': difference(control, changed, crop),
                              'searchControlChangedPixels': changed_pixels(first, control, crop),
                              'searchLogoChangedPixels': changed_pixels(control, changed, crop),
                              'startSidebarCrop': start_crop,
                              'startSidebarControlChangedPixels': changed_pixels(first, control, start_crop),
                              'startSidebarLogoChangedPixels': changed_pixels(control, changed, start_crop),
                              'endSidebarCrop': end_crop,
                              'endSidebarControlDifference': difference(first, control, end_crop),
                              'endSidebarDifference': difference(control, changed, end_crop),
                              'endSidebarControlChangedPixels': changed_pixels(first, control, end_crop),
                              'endSidebarLogoChangedPixels': changed_pixels(control, changed, end_crop),
                              'fullImageDifference': full_difference}))
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
