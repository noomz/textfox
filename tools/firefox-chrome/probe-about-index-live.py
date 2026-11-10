#!/usr/bin/env python3
"""Inventory Firefox's about:about index in disposable, network-isolated profiles.

Run the entire probe in a network namespace; it checks the Linux namespace before
launching Firefox. Navigation opens pages but does not activate their controls.
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

from marionette_driver.errors import MarionetteException
from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def assert_isolated():
    # An isolated namespace has no external interfaces; loopback may be down.
    interfaces = {name for _, name in socket.if_nameindex()}
    if interfaces - {'lo'}:
        raise RuntimeError(f'network isolation required; visible interfaces: {sorted(interfaces)}')


def check(browser_nova, newtab_nova, themed):
    assert_isolated()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-about-index-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome/config.css').write_text(':root { --tf-font-family: monospace; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False,
                 'network.captive-portal-service.enabled': False,
                 'network.connectivity-service.enabled': False}
        (profile / 'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access', '--new-instance',
                                    '--profile', str(profile), 'about:blank'],
                                   env={**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.timeout.page_load = 20
            m.set_context('chrome')
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,
                  build:Services.appinfo.appBuildID,browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['browserNova'] != browser_nova or metadata['newtabNova'] != newtab_nova):
                raise RuntimeError(f'unexpected Firefox process or profile: {metadata}')
            m.set_context('content')
            m.navigate('about:about')
            pages = m.execute_script("return [...document.querySelectorAll('a[href^=\"about:\"]')].map(a=>a.getAttribute('href')).sort()")
            if not pages or 'about:about' not in pages or len(pages) != len(set(pages)):
                raise RuntimeError(f'about index unavailable or duplicated: {pages}')
            print(json.dumps({'firefox':metadata,'themed':themed,'index':pages}),flush=True)
            for page in pages:
                error = None
                try:
                    m.navigate(page)
                except MarionetteException as exc:
                    error = {'type':type(exc).__name__, 'message':str(exc).splitlines()[0]}
                if process.poll() is not None:
                    raise RuntimeError(f'Firefox exited during {page}: {error}')
                try:
                    state = m.execute_script("""
                        const target=document.body || document.documentElement;
                        const names=new Set(['button','input','select','textarea','checkbox','menulist','moz-button','moz-page-nav-button']);
                        const controls=[...document.querySelectorAll('*')].filter(e=>names.has(e.localName));
                        const visible=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return !e.hidden && s.display!=='none' && s.visibility==='visible' && r.width>0 && r.height>0;};
                        const summarize=e=>({tag:e.localName,id:e.id,font:getComputedStyle(e).fontFamily,visible:visible(e)});
                        const shadow=controls.flatMap(host=>host.shadowRoot?[...host.shadowRoot.querySelectorAll('*')]
                          .filter(e=>names.has(e.localName)).map(e=>({...summarize(e),host:host.localName})):[]);
                        return {url:location.href,ready:document.readyState,title:document.title,body:!!document.body,
                          root:document.documentElement?.localName,target:target?.localName,font:target && getComputedStyle(target).fontFamily,
                          controls:controls.map(summarize),shadowControls:shadow};
                        """)
                except MarionetteException as exc:
                    state = None
                    error = error or {'type':type(exc).__name__, 'message':str(exc).splitlines()[0]}
                print(json.dumps({'firefox':metadata,'themed':themed,'requested':page,'error':error,'state':state}),flush=True)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--one', action='store_true', help='run only themed classic preference combination')
    args = parser.parse_args()
    if args.one:
        check(False, False, True)
    else:
        for browser_nova in (False, True):
            for newtab_nova in (False, True):
                for themed in (False, True):
                    check(browser_nova, newtab_nova, themed)
