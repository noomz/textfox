#!/usr/bin/env python3
"""Check keyboard resizing of a disposable Firefox split view."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, themed, rtl):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-split-view-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'intl.l10n.pseudo': 'bidi' if rtl else '',
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),direction:getComputedStyle(document.documentElement).direction}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['direction'] != ('rtl' if rtl else 'ltr')):
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            m.execute_script("""
                const first = gBrowser.selectedTab;
                const second = gBrowser.addTrustedTab('about:blank');
                gBrowser.addTabSplitView([first, second], {insertBefore:first});
                """, sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!gBrowser.tabpanels.splitViewSplitter && gBrowser.tabpanels.splitViewPanels.length === 2", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('split view did not initialize')
                time.sleep(.1)
            deadline = time.monotonic() + 10
            while not m.execute_script("return !!gBrowser.tabpanels.splitViewSplitter.getAttribute('aria-controls')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('split view splitter accessibility attributes did not initialize')
                time.sleep(.1)
            before = m.execute_script("""
                const split = gBrowser.tabpanels.splitViewSplitter;
                const panel = document.getElementById(gBrowser.tabpanels.splitViewPanels[0]);
                split.focus();
                const s = getComputedStyle(split), r = split.getBoundingClientRect();
                return {role:split.getAttribute('role'), controls:split.getAttribute('aria-controls'),
                        panelId:panel.id, width:panel.getBoundingClientRect().width,
                        clientWidth:panel.clientWidth, borderLeft:getComputedStyle(panel).borderLeftWidth,
                        borderRight:getComputedStyle(panel).borderRightWidth, valueNow:split.getAttribute('aria-valuenow'),
                        splitterWidth:r.width, splitterHeight:r.height,
                        focused:document.activeElement === split, display:s.display,
                        outlineStyle:s.outlineStyle, outlineWidth:s.outlineWidth};
                """, sandbox='system')
            print(json.dumps({'firefox': meta, 'themed': themed, 'before': before}), flush=True)
            if (before['role'] != 'separator' or before['controls'] != before['panelId']
                    or not before['focused'] or before['display'] == 'none'
                    or before['splitterWidth'] <= 0 or before['splitterHeight'] <= 0):
                raise RuntimeError('split view splitter is not visible and focusable')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ARROW_RIGHT if rtl else Keys.ARROW_LEFT).perform()
            focus = m.execute_script("const e=gBrowser.tabpanels.splitViewSplitter,s=getComputedStyle(e); return {visible:e.matches(':focus-visible'), style:s.outlineStyle, width:s.outlineWidth}", sandbox='system')
            if not focus['visible'] or focus['style'] == 'none' or focus['width'] == '0px':
                raise RuntimeError(f'splitter keyboard focus outline missing: {focus}')
            deadline = time.monotonic() + 5
            while True:
                after = m.execute_script("return document.getElementById(gBrowser.tabpanels.splitViewPanels[0]).getBoundingClientRect().width", sandbox='system')
                measured = m.execute_script("const p=document.getElementById(gBrowser.tabpanels.splitViewPanels[0]); return {value:gBrowser.tabpanels.splitViewSplitter.getAttribute('aria-valuenow'), clientWidth:p.clientWidth}", sandbox='system')
                if after < before['width'] and measured['value'] != before['valueNow'] and int(measured['value']) == measured['clientWidth']:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'shrink arrow did not update first split panel and accessibility value: {before} -> {after}, {measured}')
                time.sleep(.1)
            print(json.dumps({'nova': nova, 'themed': themed, 'firstPanelBefore': before['width'],
                              'firstPanelAfter': after, 'after': measured}), flush=True)
            bars = m.execute_async_script("""
                const done = arguments[arguments.length - 1];
                const split = gBrowser.selectedTab.splitview;
                Promise.all(split.tabs.map(tab => gBrowser.getFindBar(tab))).then(bars => {
                  bars.forEach(bar => bar.open());
                  const state = () => bars.map(bar => {
                    const s=getComputedStyle(bar), r=bar.getBoundingClientRect();
                    return {hidden:bar.hidden, display:s.display, width:r.width, height:r.height};
                  });
                  const before=state();
                  split.reverseTabs();
                  requestAnimationFrame(() => requestAnimationFrame(() => done({count:bars.length, before, after:state()})));
                }, e => done({error:String(e)}));
                """, sandbox='system')
            print(json.dumps({'nova': nova, 'themed': themed, 'findbars': bars}), flush=True)
            overlap = m.execute_script("""
                const panels = gBrowser.tabpanels.splitViewPanels.map(id => document.getElementById(id));
                const a=panels[0].getBoundingClientRect(), b=panels[1].getBoundingClientRect();
                const split=gBrowser.tabpanels.splitViewSplitter.getBoundingClientRect();
                return {first:{x:a.x,y:a.y,width:a.width,height:a.height},
                        second:{x:b.x,y:b.y,width:b.width,height:b.height},
                        splitter:{x:split.x,y:split.y,width:split.width,height:split.height},
                        footers:panels.map(panel => {
                          const e=panel.querySelector('split-view-footer');
                          if (!e) return null;
                          const r=e.getBoundingClientRect(), s=getComputedStyle(e);
                          return {x:r.x,y:r.y,width:r.width,height:r.height,display:s.display,
                                  visibility:s.visibility, direction:s.direction};
                        })};
                """, sandbox='system')
            print(json.dumps({'nova': nova, 'themed': themed, 'geometry': overlap}), flush=True)
            visible_footers = [f for f in overlap['footers'] if f and f['display'] != 'none' and f['visibility'] == 'visible']
            if len(visible_footers) != 1 or visible_footers[0]['width'] <= 0 or visible_footers[0]['height'] <= 0:
                raise RuntimeError('inactive split panel footer is not visible')
            a, b = overlap['first'], overlap['second']
            if a['x'] < b['x'] + b['width'] and a['x'] + a['width'] > b['x'] and a['y'] < b['y'] + b['height'] and a['y'] + a['height'] > b['y']:
                raise RuntimeError('split panel boxes overlap')
            if (bars.get('count') != 2 or any(len(bars.get(stage, [])) != 2 for stage in ('before', 'after'))
                    or any(bar['hidden'] or bar['display'] == 'none' or bar['width'] <= 0 or bar['height'] <= 0
                           for stage in ('before', 'after') for bar in bars.get(stage, []))):
                raise RuntimeError(f'findbars did not remain visible in both split panels: {bars}')
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
    for nova in (False, True):
        for themed in (False, True):
            for rtl in (False, True):
                check(nova, themed, rtl)
