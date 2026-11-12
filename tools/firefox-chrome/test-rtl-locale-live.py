#!/usr/bin/env python3
"""Check textfox in a real right-to-left Firefox locale.

Installs the signed Hebrew language pack for the running Firefox release into
disposable vanilla and textfox profiles, requests the `he` locale, and
compares mirrored browser-chrome and new-tab geometry. Unlike the bidi
pseudo-locale used elsewhere, this loads real Hebrew strings and Firefox's
own RTL locale switch.

Fetch the pack once (it is verified against Mozilla's SHA256SUMS):

  D=~/.cache/textfox-firefox-live/langpacks/<version>; mkdir -p $D; cd $D
  curl -sfO https://archive.mozilla.org/pub/firefox/releases/<version>/linux-x86_64/xpi/he.xpi
  curl -sf https://archive.mozilla.org/pub/firefox/releases/<version>/SHA256SUMS \
    | grep ' linux-x86_64/xpi/he.xpi$' > he.sha256

Cases: browser Nova off and on, horizontal and vertical tabs, vanilla and
textfox. The disposable new-tab profile may make network requests.
"""
import argparse
import hashlib
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
LANGPACK_ID = 'langpack-he@firefox.mozilla.org'
GEOMETRY = r"""
const rect = e => { if (!e) return null; const r = e.getBoundingClientRect();
  return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width, height:r.height}; };
const visible = e => e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility === 'visible';
const nav = document.getElementById('nav-bar');
const items = [...document.querySelectorAll('#nav-bar-customization-target > *')].filter(visible)
  .map(e => ({id:e.id, rect:rect(e)}));
const vertical = document.querySelector('box#vertical-tabs');
return {locale:Services.locale.appLocaleAsBCP47, dir:document.documentElement.getAttribute('dir') || getComputedStyle(document.documentElement).direction,
        navDirection:getComputedStyle(nav).direction, window:{width:innerWidth, height:innerHeight},
        nav:rect(nav), items, urlbar:rect(document.getElementById('urlbar')),
        tabs:rect(document.getElementById('tabbrowser-tabs')),
        vertical: visible(vertical) ? rect(vertical) : null,
        sidebarMain: visible(document.getElementById('sidebar-main')) ? rect(document.getElementById('sidebar-main')) : null,
        selectedTabLabel: rect(document.querySelector('.tabbrowser-tab[selected] .tab-label'))};"""
NEWTAB = r"""
const rect = e => { if (!e) return null; const r = e.getBoundingClientRect();
  return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:r.width, height:r.height}; };
const tile = document.querySelector('.top-site-outer'), title = tile?.querySelector('.title');
return {dir:document.documentElement.dir || getComputedStyle(document.documentElement).direction,
        width:innerWidth, tile:rect(tile), title:rect(title),
        titleOverflow: title ? title.scrollWidth > title.clientWidth + 1 : null};"""


def langpack_for(version):
    directory = Path.home() / '.cache' / 'textfox-firefox-live' / 'langpacks' / version
    xpi, sums = directory / 'he.xpi', directory / 'he.sha256'
    if not xpi.is_file() or not sums.is_file():
        raise SystemExit(f'missing {xpi} or {sums}; see this script\'s docstring to fetch them')
    expected = sums.read_text().split()[0]
    if hashlib.sha256(xpi.read_bytes()).hexdigest() != expected:
        raise SystemExit(f'{xpi} does not match {sums}')
    return xpi


def run(xpi, nova, vertical, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-rtl-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'extensions').mkdir()
        shutil.copy2(xpi, profile / 'extensions' / f'{LANGPACK_ID}.xpi')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'intl.locale.requested': 'he', 'extensions.autoDisableScopes': 0,
                 'sidebar.revamp': True, 'sidebar.verticalTabs': vertical,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = {**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'}
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, "
                "build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled')}",
                sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova):
                raise RuntimeError(f'unexpected Firefox process, profile, or preference: {meta}')
            if vertical:
                ready = m.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    SidebarController.promiseInitialized.then(() => done(true), e => done(String(e)));
                    """, sandbox='system')
                if ready is not True:
                    raise RuntimeError(f'sidebar initialization failed: {ready}')
            deadline = time.monotonic() + 20
            while True:
                chrome = m.execute_script(GEOMETRY, sandbox='system')
                if chrome['locale'] == 'he' and (not vertical or chrome['vertical']):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'Hebrew locale or tab strip did not apply: {chrome["locale"]}')
                time.sleep(.25)
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script("return !!document.querySelector('.top-site-outer .title')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('new-tab shortcuts did not initialize')
                time.sleep(.25)
            return meta, chrome, m.execute_script(NEWTAB)
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


def inside(inner, outer, slack=1):
    return (inner['left'] >= outer['left'] - slack and inner['right'] <= outer['right'] + slack
            and inner['top'] >= outer['top'] - slack and inner['bottom'] <= outer['bottom'] + slack)


def check(label, chrome, newtab):
    failures = []
    if chrome['dir'] != 'rtl' or chrome['navDirection'] != 'rtl':
        failures.append(f'{label}: chrome is not right-to-left: {chrome["dir"]}, {chrome["navDirection"]}')
    if newtab['dir'] != 'rtl':
        failures.append(f'{label}: new tab is not right-to-left: {newtab["dir"]}')
    # Items keep DOM order; in RTL those before the URL bar sit to its right,
    # those after it to its left. (textfox hides back/forward by default.)
    ids = [item['id'] for item in chrome['items']]
    if 'urlbar-container' not in ids:
        failures.append(f'{label}: URL bar container is not visible')
    else:
        at = ids.index('urlbar-container')
        urlbar = chrome['items'][at]['rect']
        for index, item in enumerate(chrome['items']):
            if index < at and item['rect']['left'] < urlbar['right'] - 1:
                failures.append(f'{label}: {item["id"]} is not mirrored to the right of the URL bar')
            if index > at and item['rect']['right'] > urlbar['left'] + 1:
                failures.append(f'{label}: {item["id"]} is not mirrored to the left of the URL bar')
    window = {'left': 0, 'right': chrome['window']['width'], 'top': 0, 'bottom': chrome['window']['height']}
    for item in chrome['items']:
        if not inside(item['rect'], chrome['nav']):
            failures.append(f'{label}: {item["id"]} overflows the navigation bar')
    for name in ('nav', 'urlbar', 'tabs', 'vertical', 'sidebarMain'):
        if chrome[name] and not inside(chrome[name], window):
            failures.append(f'{label}: {name} leaves the window')
    if chrome['vertical'] and chrome['vertical']['left'] < chrome['window']['width'] / 2:
        failures.append(f'{label}: vertical tabs are not on the right in RTL')
    if newtab['tile'] and newtab['title'] and not inside(newtab['title'], newtab['tile']):
        failures.append(f'{label}: new-tab shortcut title leaves its tile')
    return failures


def main():
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    version = subprocess.check_output(['firefox', '--version'], text=True).split()[-1]
    xpi = langpack_for(version)
    failures, orders = [], {}
    for nova in (False, True):
        for vertical in (False, True):
            for themed in (False, True):
                label = f'nova={nova} vertical={vertical} themed={themed}'
                try:
                    meta, chrome, newtab = run(xpi, nova, vertical, themed)
                except Exception as error:
                    failures.append(f'{label}: {error}')
                    continue
                print(json.dumps({'firefox': meta, 'nova': nova, 'vertical': vertical, 'themed': themed,
                                  'chrome': chrome, 'newtab': newtab}), flush=True)
                failures += check(label, chrome, newtab)
                # Toolbar items must keep vanilla's mirrored order in textfox.
                order = [item['id'] for item in sorted(chrome['items'], key=lambda i: -i['rect']['right'])]
                orders[(nova, vertical, themed)] = order
    for (nova, vertical, themed), order in orders.items():
        vanilla = orders.get((nova, vertical, False))
        if themed and vanilla is not None:
            shared = [i for i in vanilla if i in order]
            if [i for i in order if i in shared] != shared:
                failures.append(f'nova={nova} vertical={vertical}: textfox toolbar order {order} != vanilla {vanilla}')
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print(f'Hebrew RTL locale holds for {len(orders)} cases')


if __name__ == '__main__':
    main()
