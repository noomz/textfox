#!/usr/bin/env python3
"""Inspect shortcut hover selectors in disposable Firefox profiles.

New-tab feeds are enabled for the fixture and may make network requests.
"""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(browser_nova, newtab_nova, themed):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/content/newtab.css']
        if not selector_records:
            raise ValueError('selector inventory has no new-tab records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-shortcut-hover-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
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
            m.set_context('chrome')
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['browserNova'] != browser_nova or meta['newtabNova'] != newtab_nova):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script("return location.href === 'about:newtab' && !!document.querySelector('.top-site-outer .tile') && !!document.querySelector('.top-site-outer .title')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('shortcut fixture did not initialize')
                time.sleep(.2)

            def move(target):
                point = m.execute_script("""
                    if (arguments[0] === 'safe') return {x:innerWidth-2, y:innerHeight-2};
                    const e=document.querySelector('.top-site-outer ' + arguments[0]);
                    const r=e.getBoundingClientRect();
                    if (!r.width || !r.height) throw new Error('hover target has no geometry');
                    return {x:r.x+r.width/2, y:r.y+r.height/2};
                    """, script_args=[target])
                m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(
                    round(point['x']), round(point['y'])).perform()
                deadline = time.monotonic() + 10
                repositioned = 0
                while True:
                    result = m.execute_script("""
                        const site=document.querySelector('.top-site-outer');
                        const tile=site.querySelector('.tile'), title=site.querySelector('.title');
                        const inner=site.querySelector('.top-site-inner'), style=getComputedStyle(tile);
                        const reference=document.createElement('span');
                        reference.style.backgroundColor='var(--newtab-element-hover-color)';
                        tile.append(reference);
                        const tokenColor=getComputedStyle(reference).backgroundColor;
                        reference.remove();
                        return {siteHover:site.matches(':hover'), innerHover:inner.matches(':hover'),
                          tileHover:tile.matches(':hover'), titleHover:title.matches(':hover'),
                          tileBackground:style.backgroundColor, tokenColor,
                          tileRect:tile.getBoundingClientRect().toJSON(),
                          titleRect:title.getBoundingClientRect().toJSON(), focused:document.hasFocus()};
                        """)
                    expected = (not result['siteHover'] if target == 'safe' else
                                result['titleHover'] and not result['tileHover'] if target == '.title' else
                                result['tileHover'])
                    if expected:
                        result['repositioned'] = repositioned
                        return result
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'pointer did not reach {target}: point={point}, state={result}')
                    if target != 'safe':
                        rect = result['titleRect' if target == '.title' else 'tileRect']
                        current = {'x': rect['x'] + rect['width'] / 2,
                                   'y': rect['y'] + rect['height'] / 2}
                        if abs(current['x'] - point['x']) > 2 or abs(current['y'] - point['y']) > 2:
                            point = current
                            m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).pointer_move(
                                round(point['x']), round(point['y'])).perform()
                            repositioned += 1
                    time.sleep(.1)

            icon = m.execute_script("""
                const e=document.querySelector('.top-site-outer .top-site-icon');
                if (!e) return null;
                const s=getComputedStyle(e), r=e.getBoundingClientRect();
                const result={background:s.backgroundColor, image:s.backgroundImage,
                  display:s.display, visibility:s.visibility, width:r.width, height:r.height};
                const value=e.style.getPropertyValue('background-color');
                const priority=e.style.getPropertyPriority('background-color');
                e.style.setProperty('background-color', 'rgb(19, 83, 137)', 'important');
                result.inlineIntervention=getComputedStyle(e).backgroundColor;
                if (value) e.style.setProperty('background-color', value, priority);
                else e.style.removeProperty('background-color');
                result.restored=getComputedStyle(e).backgroundColor;
                return result;
                """)
            if not icon or icon['width'] <= 0 or icon['height'] <= 0:
                raise RuntimeError(f'shortcut icon fixture unavailable: {icon}')
            if (icon['restored'] != icon['background']
                    or themed and icon['inlineIntervention'] != 'rgba(0, 0, 0, 0)'
                    or not themed and icon['inlineIntervention'] != 'rgb(19, 83, 137)'):
                raise RuntimeError(f'shortcut icon background intervention was inconclusive: {icon}')
            baseline = move('safe')
            title = move('.title')
            tile = move('.tile')
            selector_results=selector_query(m,selector_records) if selector_query else []
            if selector_query:
                matched=[r for r in selector_results if r['selector']=='&:hover' and '.tile' in r['expandedSelector']]
                if len(matched)!=1 or matched[0]['status']!='structural-match':
                    raise RuntimeError(f'tile hover selector did not match: {matched}')
            print(json.dumps({'firefox': meta, 'themed': themed, 'icon':icon, 'baseline': baseline,
                              'title': title, 'tile': tile,'selectorRecords':selector_results}), flush=True)
            if not title['innerHover'] or not tile['innerHover']:
                raise RuntimeError('shortcut inner hover selector did not match')
            if themed and tile['tileBackground'] != tile['tokenColor']:
                raise RuntimeError('textfox tile hover did not use the configured hover token')
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
    for browser_nova in (False, True):
        for newtab_nova in (False, True):
            for themed in (False, True):
                check(browser_nova, newtab_nova, themed)
