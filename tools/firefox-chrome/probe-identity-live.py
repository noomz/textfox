#!/usr/bin/env python3
"""Inspect secure and mixed-content identity selectors with disposable local TLS.

Requires openssl and certutil. A fixture CA is trusted only in the temporary
Firefox profile. Both fixture hostnames resolve to the local server.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
HOST = 'textfox-identity.invalid'
MIXED_HOST = 'textfox-mixed.invalid'


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def check(nova, display):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-identity-') as directory:
        profile = Path(directory)
        if display is not None:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-display-urlbar-icons: {display}; }}\n')
        ca, ca_key = profile / 'fixture-ca.pem', profile / 'fixture-ca.key'
        cert, key, csr, ext = (profile / name for name in ('fixture-cert.pem', 'fixture-key.pem', 'fixture.csr', 'fixture.ext'))
        ext.write_text(f'subjectAltName=DNS:{HOST}\nextendedKeyUsage=serverAuth\n')
        run('openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(ca_key),'-out',str(ca),
            '-subj','/CN=Textfox disposable fixture CA','-days','1',
            '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
        run('openssl','req','-new','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(csr),
            '-subj',f'/CN={HOST}')
        run('openssl','x509','-req','-in',str(csr),'-CA',str(ca),'-CAkey',str(ca_key),'-CAcreateserial',
            '-out',str(cert),'-days','1','-extfile',str(ext))
        run('certutil','-N','-d',f'sql:{profile}','--empty-password')
        run('certutil','-A','-d',f'sql:{profile}','-n','Textfox disposable fixture CA','-t','C,,','-i',str(ca))

        class Handler(BaseHTTPRequestHandler):
            mixed_port = None
            mixed_requests = 0

            def do_GET(self):
                if self.path == '/mixed-script.js':
                    type(self).mixed_requests += 1
                    body = b'window.textfoxMixedScriptRan = true;'
                    content_type = 'application/javascript'
                else:
                    mixed = (f'<script src="http://{MIXED_HOST}:{self.mixed_port}/mixed-script.js"></script>'
                             if self.path == '/mixed' else '')
                    body = f'<!doctype html><title>textfox identity</title>{mixed}<p>identity fixture</p>'.encode()
                    content_type = 'text/html'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        Handler.mixed_port = http.server_port
        https = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        https.socket = tls.wrap_socket(https.socket, server_side=True)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (http, https)]
        for thread in threads:
            thread.start()
        prefs = {'marionette.port':port, 'browser.nova.enabled':nova,
                 'network.proxy.type':0, 'network.dns.localDomains':f'{HOST},{MIXED_HOST}',
                 'dom.security.https_first':False, 'security.mixed_content.block_active_content':True,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = None
        m = None
        try:
            process = subprocess.Popen(['firefox','--marionette','-remote-allow-system-access',
                                        '--new-instance','--profile',str(profile),'about:blank'],
                                       env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova:
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            samples = {}
            for name in ('secure', 'mixed'):
                url = f'https://{HOST}:{https.server_port}/{name}'
                m.set_context('content')
                m.navigate(url)
                body = m.execute_script('return {url:location.href,title:document.title,mixedRan:!!window.textfoxMixedScriptRan}')
                if body['url'] != url or body['title'] != 'textfox identity' or body['mixedRan']:
                    raise RuntimeError(f'identity fixture failed to load securely: {body}')
                m.set_context('chrome')
                deadline = time.monotonic() + 15
                while True:
                    state = m.execute_script("""
                        const box=document.getElementById('identity-box');
                        const ids=['identity-permission-box','permissions-granted-icon','identity-icon'];
                        return {uri:gBrowser.currentURI.spec,classes:[...box.classList],
                          pageProxy:box.getAttribute('pageproxystate'),
                          targets:Object.fromEntries(ids.map(id=>{
                            const e=document.getElementById(id); if(!e) return [id,null];
                            const s=getComputedStyle(e),r=e.getBoundingClientRect();
                            return [id,{display:s.display,visibility:s.visibility,width:r.width,height:r.height,
                              hasSharingIcon:e.hasAttribute('hasSharingIcon'),image:s.listStyleImage}];
                          }))};
                        """, sandbox='system')
                    expected_class = 'mixedActiveBlocked' if name == 'mixed' else 'verifiedDomain'
                    if state['uri'] == url and state['pageProxy'] == 'valid' and expected_class in state['classes']:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'{name} identity state did not initialize: {state}')
                    time.sleep(.1)
                permission = state['targets']['identity-permission-box']
                granted = state['targets']['permissions-granted-icon']
                if (not permission or not granted or not state['targets']['identity-icon']
                        or permission['hasSharingIcon']
                        or (display is not None and (permission['display'] != display or granted['display'] != display))):
                    raise RuntimeError(f'{name} identity selector option did not apply: {state}')
                branch_control = None
                if name == 'mixed':
                    if Handler.mixed_requests:
                        raise RuntimeError(f'mixed fixture script unexpectedly reached server: {Handler.mixed_requests}')
                    branch_control = m.execute_script("""
                        const box=document.getElementById('identity-box'), original=box.getAttribute('class');
                        const measure=()=>({classes:[...box.classList],
                          permission:getComputedStyle(document.getElementById('identity-permission-box')).display,
                          granted:getComputedStyle(document.getElementById('permissions-granted-icon')).display});
                        try {
                          box.classList.remove('verifiedDomain');
                          const mixedOnly=measure();
                          box.classList.remove('mixedActiveBlocked');
                          return {mixedOnly, neither:measure()};
                        } finally { box.setAttribute('class',original); }
                        """, sandbox='system')
                    if ('mixedActiveBlocked' not in branch_control['mixedOnly']['classes']
                            or 'verifiedDomain' in branch_control['mixedOnly']['classes']):
                        raise RuntimeError(f'mixed branch control did not isolate class: {branch_control}')
                    if display is not None:
                        mixed_only, neither = branch_control['mixedOnly'], branch_control['neither']
                        if (mixed_only['permission'] != display or mixed_only['granted'] != display
                                or (neither['permission'] == display and neither['granted'] == display)):
                            raise RuntimeError(f'mixed branch intervention inconclusive: {branch_control}')
                samples[name] = {'page':body, 'identity':state, 'mixedRequests':Handler.mixed_requests,
                                 'mixedBranchControl':branch_control}
            print(json.dumps({'firefox':meta,'display':display,'samples':samples}),flush=True)
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                for server in (http, https):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=5)


if __name__ == '__main__':
    for nova in (False, True):
        for display in (None, 'none', 'flex'):
            check(nova, display)
