#!/usr/bin/env python3
"""Inspect protection and identity panels on a disposable loopback origin."""
import http.server
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Textfox panel fixture</title><p>local fixture</p>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def check(nova, themed):
    selector_path = os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records = None
    selector_query = None
    if selector_path:
        selector_records = [r for r in json.loads(Path(selector_path).read_text()) if r['file'] == 'chrome/icons.css']
        if not selector_records:
            raise ValueError('selector inventory has no icon records')
        selector_query = runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix='tf-site-panel-') as directory:
            profile = Path(directory)
            if themed:
                shutil.copytree(ROOT / 'chrome', profile / 'chrome')
                (profile / 'chrome/config.css').write_text(':root { --tf-display-urlbar-icons: flex; }\n')
            prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                     'browser.urlbar.trustPanel.featureGate': False,
                     'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                     'browser.shell.checkDefaultBrowser': False}
            (profile / 'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
            process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access', '--new-instance',
                                        '--profile', str(profile), 'about:blank'],
                                       env={**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            m = None
            try:
                m = connect(port)
                m.set_context('chrome')
                meta = m.execute_script("""
                    return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,
                      build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                      trustPanel:Services.prefs.getBoolPref('browser.urlbar.trustPanel.featureGate')};
                    """, sandbox='system')
                if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova or meta['trustPanel']:
                    raise RuntimeError(f'unexpected Firefox process or profile: {meta}')
                m.set_context('content')
                m.navigate(f'http://127.0.0.1:{server.server_port}/')
                m.execute_script("localStorage.setItem('textfox-panel-fixture','value')")
                m.set_context('chrome')
                deadline = time.monotonic() + 15
                while not m.execute_script("return document.getElementById('identity-box').getAttribute('pageproxystate')==='valid'", sandbox='system'):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('identity page proxy did not become valid')
                    time.sleep(.1)
                initial_shield = m.execute_script("const e=document.getElementById('tracking-protection-icon-container'),s=getComputedStyle(e),r=e.getBoundingClientRect();return {display:s.display,width:r.width,height:r.height,token:getComputedStyle(document.documentElement).getPropertyValue('--tf-display-urlbar-icons').trim()}", sandbox='system')
                if initial_shield['display'] == 'none' or initial_shield['width'] <= 0 or initial_shield['height'] <= 0:
                    raise RuntimeError(f'protection icon unavailable: {initial_shield}')
                results = {}
                for button_id, popup_id, targets in (
                    ('tracking-protection-icon-container', 'protections-popup',
                     ('protections-popup-settings-button', 'protections-popup-show-report-stack')),
                    ('identity-icon-box', 'identity-popup', ('identity-popup-clear-sitedata-button',)),
                ):
                    m.find_element('id', button_id).click()
                    deadline = time.monotonic() + 15
                    while not m.execute_script("return document.getElementById(arguments[0])?.state === 'open'", script_args=[popup_id], sandbox='system'):
                        if time.monotonic() >= deadline:
                            diagnostic = m.execute_script("const e=document.getElementById(arguments[0]),b=document.getElementById(arguments[1]); return {state:e?.state,tag:e?.localName,button:b?.outerHTML,ancestors:[b,b?.parentElement,b?.parentElement?.parentElement].filter(Boolean).map(x=>({id:x.id,display:getComputedStyle(x).display,visibility:getComputedStyle(x).visibility,rect:x.getBoundingClientRect().toJSON()}))}", script_args=[popup_id,button_id], sandbox='system')
                            raise RuntimeError(f'{popup_id} did not open: {diagnostic}')
                        time.sleep(.1)
                    sample = m.execute_script("""
                        const popup=document.getElementById(arguments[0]);
                        return arguments[1].map(id=>{const e=document.getElementById(id);
                          if(!e)return {id,found:false};const s=getComputedStyle(e),r=e.getBoundingClientRect();
                          return {id,found:true,inPopup:popup.contains(e),hidden:e.hidden,display:s.display,
                            visibility:s.visibility,width:r.width,height:r.height,image:s.listStyleImage,background:s.backgroundImage};});
                        """, script_args=[popup_id, targets], sandbox='system')
                    if any(not item['found'] or not item['inPopup'] or item['hidden'] or item['display']=='none'
                           or item['visibility']!='visible' or item['width']<=0 or item['height']<=0 for item in sample):
                        raise RuntimeError(f'{popup_id} targets unavailable: {sample}')
                    resources = []
                    if themed:
                        resources = m.execute_async_script("""
                            const done=arguments[arguments.length-1],ids=arguments[0], urls=ids.map(id=>getComputedStyle(document.getElementById(id)).listStyleImage);
                            urls.push('url("chrome://global/skin/icons/textfox-nonexistent-resource.svg")');
                            Promise.all(urls.map(raw=>new Promise(resolve=>{
                              const match=raw.match(/^url\\(["']?(.*?)["']?\\)$/);
                              if(!match){resolve({raw,loaded:false,reason:'not a URL'});return;}
                              const image=new Image(),timer=setTimeout(()=>resolve({raw,loaded:false,reason:'timeout'}),5000);
                              image.onload=()=>{clearTimeout(timer);resolve({raw,loaded:true,width:image.naturalWidth,height:image.naturalHeight});};
                              image.onerror=()=>{clearTimeout(timer);resolve({raw,loaded:false,reason:'error'});};
                              image.src=match[1];
                            }))).then(done);
                            """, script_args=[targets], sandbox='system')
                        negative = resources.pop()
                        if negative.get('loaded') or negative.get('reason') != 'error' or any(not r['loaded'] or r['width']<=0 or r['height']<=0 for r in resources):
                            raise RuntimeError(f'{popup_id} icon resources unavailable: {resources}, {negative}')
                    selectors = selector_query(m, selector_records) if selector_query else []
                    results[popup_id] = {'targets': sample, 'resources': resources, 'selectorRecords': selectors}
                    m.execute_script("document.getElementById(arguments[0]).hidePopup()", script_args=[popup_id], sandbox='system')
                    deadline = time.monotonic() + 10
                    while m.execute_script("return document.getElementById(arguments[0]).state", script_args=[popup_id], sandbox='system') != 'closed':
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'{popup_id} did not close')
                        time.sleep(.1)
                print(json.dumps({'firefox': meta, 'themed': themed, 'initialShield': initial_shield,
                                  'panels': results}), flush=True)
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
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


if __name__ == '__main__':
    for nova in (False, True):
        for themed in (False, True):
            check(nova, themed)
