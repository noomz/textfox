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
            geo_requests = 0

            def do_POST(self):
                if self.path != '/geo':
                    self.send_error(404)
                    return
                type(self).geo_requests += 1
                body = b'{"location":{"lat":0,"lng":0},"accuracy":100}'
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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
                 'geo.prompt.testing':False,
                 'geo.provider.testing':True, 'geo.wifi.scan':False,
                 'geo.provider.network.url':f'http://127.0.0.1:{http.server_port}/geo',
                 'media.navigator.streams.fake':True, 'media.navigator.permission.fake':True,
                 'media.navigator.permission.disabled':False,
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
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  geoProviderTesting:Services.prefs.getBoolPref('geo.provider.testing'),
                  geoWifiScan:Services.prefs.getBoolPref('geo.wifi.scan'),
                  geoProviderURL:Services.prefs.getStringPref('geo.provider.network.url'),
                  fakeMedia:Services.prefs.getBoolPref('media.navigator.streams.fake'),
                  mediaPermissionDisabled:Services.prefs.getBoolPref('media.navigator.permission.disabled'),
                  mediaPermissionFake:Services.prefs.getBoolPref('media.navigator.permission.fake')};
                """, sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova
                    or not meta['geoProviderTesting'] or meta['geoWifiScan']
                    or not meta['fakeMedia'] or not meta['mediaPermissionFake'] or meta['mediaPermissionDisabled']
                    or meta['geoProviderURL'] != f'http://127.0.0.1:{http.server_port}/geo'):
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
            m.set_context('content')
            m.navigate(f'https://{HOST}:{https.server_port}/secure')
            m.execute_script("""
                const root=document.documentElement;
                root.setAttribute('data-textfox-geo-positions','0');
                const watch=navigator.geolocation.watchPosition(
                  ()=>root.setAttribute('data-textfox-geo-positions',String(Number(root.getAttribute('data-textfox-geo-positions'))+1)),
                  e=>root.setAttribute('data-textfox-geo-error',`${e.code}: ${e.message}`));
                root.setAttribute('data-textfox-geo-watch',String(watch));
                """)
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while not m.execute_script("return !!PopupNotifications.getNotification('geolocation') && PopupNotifications.panel.state === 'open' && !!PopupNotifications.panel.querySelector('.popup-notification-primary-button')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('geolocation permission prompt did not open')
                time.sleep(.1)
            allow = m.find_element('css selector', '#notification-popup .popup-notification-primary-button')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(allow).perform()
            m.set_context('content')
            deadline = time.monotonic() + 20
            while True:
                observed = m.execute_script("""
                    const r=document.documentElement;
                    return {url:location.href,ready:document.readyState,
                      positions:Number(r.getAttribute('data-textfox-geo-positions')),
                      error:r.getAttribute('data-textfox-geo-error')};
                    """)
                geo = observed
                if geo['positions'] > 0:
                    break
                if geo['error'] or time.monotonic() >= deadline:
                    raise RuntimeError(f'local fake geolocation did not initialize: {observed}, requests={Handler.geo_requests}')
                time.sleep(.1)
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while True:
                sharing = m.execute_script("""
                    const box=document.getElementById('identity-box');
                    const permission=document.getElementById('identity-permission-box');
                    const granted=document.getElementById('permissions-granted-icon');
                    const icon=document.getElementById('geo-sharing-icon'), sibling=granted.nextElementSibling;
                    const r=permission.getBoundingClientRect();
                    return {classes:[...box.classList],pageProxy:box.getAttribute('pageproxystate'),
                      hasSharingIcon:permission.hasAttribute('hasSharingIcon'), sharing:icon.getAttribute('sharing'),
                      siblingMatches:sibling?.matches('box:has(image[sharing="true"])'),
                      siblingMarginLeft:sibling && getComputedStyle(sibling).marginLeft,
                      permissionDisplay:getComputedStyle(permission).display,
                      grantedDisplay:getComputedStyle(granted).display,
                      permissionWidth:r.width,permissionHeight:r.height};
                    """, sandbox='system')
                if sharing['hasSharingIcon'] and sharing['sharing'] == 'true':
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'geolocation sharing indicator did not initialize: {sharing}')
                time.sleep(.1)
            if (not Handler.geo_requests or not sharing['siblingMatches'] or sharing['permissionDisplay'] == 'none'
                    or sharing['permissionWidth'] <= 0 or sharing['permissionHeight'] <= 0
                    or (display is not None and (sharing['grantedDisplay'] != display or sharing['siblingMarginLeft'] != '-4px'))):
                raise RuntimeError(f'geolocation sharing selector fixture failed: {sharing}, requests={Handler.geo_requests}')
            m.set_context('content')
            m.execute_script("navigator.geolocation.clearWatch(Number(document.documentElement.getAttribute('data-textfox-geo-watch')))")
            m.set_context('chrome')
            media_url = f'https://{HOST}:{https.server_port}/secure?media=1'
            m.execute_script('gBrowser.selectedTab=gBrowser.addTrustedTab(arguments[0])', script_args=[media_url], sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 15
            while m.execute_script('return location.href') != media_url:
                if time.monotonic() >= deadline:
                    raise RuntimeError('fake-media fixture page did not load')
                time.sleep(.1)
            m.execute_script("""
                const root=document.documentElement;
                navigator.mediaDevices.getUserMedia({video:true,audio:true}).then(stream=>{
                  root.setAttribute('data-textfox-media-tracks',stream.getTracks().map(t=>t.kind).sort().join(','));
                  document.addEventListener('textfox-stop-stream',()=>{
                    stream.getTracks().forEach(t=>t.stop());
                    root.setAttribute('data-textfox-media-stopped',String(stream.getTracks().every(t=>t.readyState === 'ended')));
                  },{once:true});
                },error=>root.setAttribute('data-textfox-media-error',`${error.name}: ${error.message}`));
                """)
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while not m.execute_script("return !!PopupNotifications.getNotification('webRTC-shareDevices') && PopupNotifications.panel.state === 'open' && !!PopupNotifications.panel.querySelector('.popup-notification-primary-button')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('fake-media permission prompt did not open')
                time.sleep(.1)
            allow = m.find_element('css selector', '#notification-popup .popup-notification-primary-button')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(allow).perform()
            m.set_context('content')
            deadline = time.monotonic() + 20
            while True:
                media = m.execute_script("""
                    const r=document.documentElement;
                    return {url:location.href,tracks:r.getAttribute('data-textfox-media-tracks'),
                      error:r.getAttribute('data-textfox-media-error')};
                    """)
                if media['tracks'] == 'audio,video':
                    break
                if media['error'] or time.monotonic() >= deadline:
                    raise RuntimeError(f'fake media did not initialize: {media}')
                time.sleep(.1)
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while True:
                media_sharing = m.execute_script("""
                    const box=document.getElementById('identity-box');
                    const permission=document.getElementById('identity-permission-box');
                    const granted=document.getElementById('permissions-granted-icon');
                    const icon=document.getElementById('webrtc-sharing-icon'), sibling=granted.nextElementSibling;
                    const r=permission.getBoundingClientRect();
                    return {classes:[...box.classList],pageProxy:box.getAttribute('pageproxystate'),
                      hasSharingIcon:permission.hasAttribute('hasSharingIcon'),sharing:icon.getAttribute('sharing'),
                      siblingMatches:sibling?.matches('box:has(image[sharing="true"])'),
                      siblingMarginLeft:sibling && getComputedStyle(sibling).marginLeft,
                      permissionDisplay:getComputedStyle(permission).display,
                      grantedDisplay:getComputedStyle(granted).display,
                      permissionWidth:r.width,permissionHeight:r.height};
                    """, sandbox='system')
                if media_sharing['hasSharingIcon'] and media_sharing['sharing']:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'fake-media sharing indicator did not initialize: {media_sharing}')
                time.sleep(.1)
            if (media_sharing['sharing'] != 'camera' or media_sharing['siblingMatches']
                    or media_sharing['permissionDisplay'] == 'none' or media_sharing['permissionWidth'] <= 0
                    or media_sharing['permissionHeight'] <= 0 or (display is not None and media_sharing['grantedDisplay'] != display)):
                raise RuntimeError(f'fake-media identity selector fixture failed: {media_sharing}')
            media_control = None
            if display is not None:
                media_control = m.execute_script("""
                    const sibling=document.getElementById('permissions-granted-icon').nextElementSibling;
                    const css='#identity-box[pageproxystate="valid"].verifiedDomain #permissions-granted-icon + box:has(image[sharing]) { margin-left: -4px !important; }';
                    const uri='data:text/css,'+encodeURIComponent(css);
                    window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                    try { return getComputedStyle(sibling).marginLeft; }
                    finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                    """, sandbox='system')
                if media_control != '-4px':
                    raise RuntimeError(f'fake-media sharing selector intervention failed: {media_control}')
            m.set_context('content')
            stopped = m.execute_script("""
                document.dispatchEvent(new Event('textfox-stop-stream'));
                return document.documentElement.getAttribute('data-textfox-media-stopped');
                """)
            if stopped != 'true':
                raise RuntimeError(f'fake media tracks did not stop: {stopped}')
            print(json.dumps({'firefox':meta,'display':display,'samples':samples,
                              'geo':geo,'sharing':sharing,'geoRequests':Handler.geo_requests,
                              'media':media,'mediaSharing':media_sharing,'mediaControl':media_control,
                              'mediaStopped':stopped}),flush=True)
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
