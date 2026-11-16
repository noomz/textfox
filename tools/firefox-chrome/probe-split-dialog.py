#!/usr/bin/env python3
"""Inspect a real inactive split-panel alert in disposable Firefox profiles."""
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


def check(nova, mode):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-split-dialog-') as directory:
        profile = Path(directory)
        if mode != 'vanilla':
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        if mode == 'clip-control':
            css = profile / 'chrome' / 'browser.css'
            source = css.read_text()
            if source.count('overflow: visible !important;') != 2:
                raise RuntimeError('split overflow control no longer matches expected CSS')
            css.write_text(source.replace('overflow: visible !important;', 'overflow: clip !important;'))
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
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
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova):
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            m.set_context('content')
            m.navigate("data:text/html,<body style='height:100vh'><script>addEventListener('pointerdown',()=>document.title='active-pointer')</script></body>")
            m.set_context('chrome')
            m.execute_script("""
                window.resizeTo(720, 600);
                const active = gBrowser.selectedTab;
                window.__tfActiveBrowser = active.linkedBrowser;
                const alertTab = gBrowser.addTrustedTab("data:text/html,<script>setTimeout(()=>alert('split-probe-alert'),5000)</script>");
                gBrowser.addTabSplitView([alertTab, active], {insertBefore:active});
                gBrowser.selectedTab = active;
                """, sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("""
                const browser=document.querySelector('browser[tabDialogShowing]');
                return !!browser && gBrowser.getTabDialogBox(browser).getContentDialogManager()._dialogs.length === 1;
                """, sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('inactive split alert did not open')
                time.sleep(.2)
            ready = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                const browser=document.querySelector('browser[tabDialogShowing]');
                const dialog=gBrowser.getTabDialogBox(browser).getContentDialogManager()._dialogs[0];
                dialog._dialogReady.then(() => done({
                  url:dialog._frame.currentURI?.spec,
                  hasMessage:!!dialog._frame.contentDocument?.documentElement?.textContent?.includes('split-probe-alert')
                }), error => done({error:String(error)}));
                """, sandbox='system')
            if ready.get('url') != 'chrome://global/content/commonDialog.xhtml' or not ready.get('hasMessage'):
                raise RuntimeError(f'expected alert dialog is not ready: {ready}')
            result = m.execute_script("""
                const browser=document.querySelector('browser[tabDialogShowing]');
                const inactive=browser.closest('.split-view-panel');
                const active=document.querySelector('.split-view-panel.deck-selected');
                const dialog=gBrowser.getTabDialogBox(browser).getContentDialogManager()._dialogs[0];
                const box=dialog._frame.closest('.dialogBox');
                const p=inactive.getBoundingClientRect(), a=active.getBoundingClientRect(), r=box.getBoundingClientRect();
                const x=a.left+20, y=r.top+r.height/2;
                const content=active.querySelector('browser[type="content"]').getBoundingClientRect();
                const isDialogHit=element => !!element && (element===box || box.contains(element));
                const innerX=Math.max(p.left+2,r.left+10);
                return {dialogTabSelected:browser===gBrowser.selectedBrowser,
                  inactiveSelected:inactive.classList.contains('deck-selected'),
                  viewport:{width:innerWidth,height:innerHeight},
                  inactive:{left:p.left,right:p.right,top:p.top,bottom:p.bottom},
                  active:{left:a.left,right:a.right,top:a.top,bottom:a.bottom},
                  activeContent:{left:content.left,right:content.right,top:content.top,bottom:content.bottom},
                  dialog:{left:r.left,right:r.right,top:r.top,bottom:r.bottom},
                  point:{x,y},
                  insideControlHit:isDialogHit(document.elementFromPoint(innerX,y)),
                  activePanelHitDialog:isDialogHit(document.elementFromPoint(x,y))};
                """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'mode': mode, 'ready': ready, 'result': result}), flush=True)
            point, inactive, active, dialog = result['point'], result['inactive'], result['active'], result['dialog']
            content = result['activeContent']
            if (result['dialogTabSelected'] or result['inactiveSelected'] or not result['insideControlHit']
                    or not (inactive['right'] < point['x'] < active['right'])
                    or not (active['left'] < point['x'] < dialog['right'])
                    or not (dialog['left'] < point['x'] and dialog['top'] < point['y'] < dialog['bottom'])
                    or not (active['top'] < point['y'] < active['bottom'])
                    or not (content['left'] < point['x'] < content['right']
                            and content['top'] < point['y'] < content['bottom'])):
                raise RuntimeError(f'alert or hit-test fixture is inconclusive: {result}')
            m.execute_script("""
                const browser=document.querySelector('browser[tabDialogShowing]');
                const dialog=gBrowser.getTabDialogBox(browser).getContentDialogManager()._dialogs[0];
                window.__tfDialogPointer=0;
                dialog._frame.contentDocument.addEventListener('pointerdown', () => window.__tfDialogPointer++, true);
                """, sandbox='system')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).pointer_move(
                round(point['x']), round(point['y'])).pointer_down().pointer_up().perform()
            deadline = time.monotonic() + 5
            while True:
                pointer = m.execute_script("return {activeTitle:window.__tfActiveBrowser.contentTitle, dialogPointers:window.__tfDialogPointer}", sandbox='system')
                if pointer['activeTitle'] == 'active-pointer' or pointer['dialogPointers']:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'pointer event was not observed: {pointer}')
                time.sleep(.1)
            if (pointer['activeTitle'] == 'active-pointer') == (pointer['dialogPointers'] > 0):
                raise RuntimeError(f'pointer event receiver is ambiguous: {pointer}')
            print(json.dumps({'nova': nova, 'mode': mode, 'pointer': pointer}), flush=True)
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
        for mode in ('vanilla', 'textfox', 'clip-control'):
            check(nova, mode)
