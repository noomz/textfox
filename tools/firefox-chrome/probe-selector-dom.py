#!/usr/bin/env python3
"""Sample expanded selector candidates in several chrome and new-tab DOM states.

Input is JSON from expand-rule-selectors.py. Does not pierce shadow roots,
inspect pseudo-elements, or prove cascade, painting, or all UI states.
New-tab feeds may make network requests.
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

from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def query(m, records):
    results = []
    for offset in range(0, len(records), 40):
        chunk = records[offset:offset + 40]
        results.extend(m.execute_script("""
            return arguments[0].map(record=>{
              const selector=record.expandedSelector;
              const conditions=record.conditions.map(c=>{
                if(c.startsWith('@media ')) {
                  const m=matchMedia(c.slice(7)); return {rule:c,matches:m.matches,media:m.media};
                }
                return {rule:c,matches:null,media:null};
              });
              if(selector.includes('::')) return {status:'pseudo-not-queried',conditions};
              try {
                const nodes=[...document.querySelectorAll(selector)];
                const visible=nodes.filter(e=>{
                  const s=getComputedStyle(e),r=e.getBoundingClientRect();
                  return s.display !== 'none' && s.visibility === 'visible' && r.width > 0 && r.height > 0;
                });
                return {status:nodes.length?'structural-match':'not-found-in-sampled-document',count:nodes.length,
                  visible:visible.length,conditions};
              } catch(error) { return {status:'invalid-query',error:String(error),conditions}; }
            });
            """, script_args=[chunk], sandbox='system'))
    return [{'file':record['file'],'line':record['line'],'selector':record['selector'],
             'expandedSelector':record['expandedSelector'],**result}
            for record,result in zip(records,results,strict=True)]


def check(browser_nova, newtab_nova, records):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-selector-dom-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port,'browser.nova.enabled':browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled':newtab_nova,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds':True,
                 'browser.search.suggest.enabled':False,'browser.urlbar.suggest.searches':False,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k,v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox','--marionette','-remote-allow-system-access',
                                    '--new-instance','--profile',str(profile),'about:blank'],
                                   env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['browserNova'] != browser_nova or meta['newtabNova'] != newtab_nova):
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            chrome_records = [r for r in records if not r['file'].startswith('chrome/content/')]
            content_records = [r for r in records if r['file'].startswith('chrome/content/')]
            chrome_results = query(m, chrome_records)
            chrome_states = {}
            m.execute_script('PanelUI.show()', sandbox='system')
            deadline = time.monotonic() + 15
            while m.execute_script("return document.getElementById('appMenu-popup').state", sandbox='system') != 'open':
                if time.monotonic() >= deadline:
                    raise RuntimeError('app menu did not open')
                time.sleep(.1)
            chrome_states['app-menu'] = query(m, chrome_records)
            m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('appMenu-popup').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('app menu did not close')
                time.sleep(.1)
            m.execute_script("gURLBar.focus(); gURLBar.search('mozilla')", sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("return gURLBar.view.isOpen && !!document.querySelector('#urlbar .urlbarView-row')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('URL bar results did not open')
                time.sleep(.1)
            chrome_states['urlbar-results'] = query(m, chrome_records)
            m.execute_script('gURLBar.view.close()', sandbox='system')
            m.execute_script('SidebarController.show("viewBookmarksSidebar")', sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("const e=document.getElementById('sidebar-box'); return e.getAttribute('sidebarcommand') === 'viewBookmarksSidebar' && !e.hidden", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('bookmarks sidebar did not open')
                time.sleep(.1)
            chrome_states['bookmarks-sidebar'] = query(m, chrome_records)
            opened=m.execute_async_script("""
                const done=arguments[arguments.length-1];
                gBrowser.getFindBar(gBrowser.selectedTab).then(bar=>{bar.open();done(true);},error=>done(String(error)));
                """, sandbox='system')
            if opened is not True:
                raise RuntimeError(f'findbar did not open: {opened}')
            deadline = time.monotonic() + 10
            while not m.execute_script("return !!document.querySelector('findbar:not([hidden])')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('findbar did not become visible')
                time.sleep(.1)
            chrome_states['findbar'] = query(m, chrome_records)
            m.set_context('content')
            m.navigate('data:text/html,<a id="tf-link" href="https://example.invalid/">selector fixture</a>')
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).click(m.find_element('id','tf-link'),button=2).perform()
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while m.execute_script("return document.getElementById('contentAreaContextMenu').state", sandbox='system') != 'open':
                if time.monotonic() >= deadline:
                    raise RuntimeError('link context menu did not open')
                time.sleep(.1)
            chrome_states['link-context'] = query(m, chrome_records)
            m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('contentAreaContextMenu').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('link context menu did not close')
                time.sleep(.1)
            m.execute_script('gBrowser.selectedTab=gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script("return location.href === 'about:newtab' && !!document.querySelector('.top-site-outer .tile')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('new-tab content did not initialize')
                time.sleep(.2)
            content_results = query(m, content_records)
            print(json.dumps({'firefox':meta,'chrome':chrome_results,'chromeStates':chrome_states,
                              'newtab':content_results}),flush=True)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory',type=Path,help='output of expand-rule-selectors.py')
    args = parser.parse_args()
    records = json.loads(args.inventory.read_text())
    if not records:
        raise ValueError('selector inventory is empty')
    for browser_nova in (False,True):
        for newtab_nova in (False,True):
            check(browser_nova,newtab_nova,records)


if __name__ == '__main__':
    main()
