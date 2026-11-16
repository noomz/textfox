#!/usr/bin/env python3
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from ff_inspect import connect
from paint_display import Display

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ('<style>body{margin:12px}#author{font-family:sans-serif;font-size:22px}'
          '#mono{font-family:monospace}</style><main>'
          '<p id="plain">Default website AaIl10</p>'
          '<h2 id="heading">Heading ร่างพระราชบัญญัติ</h2>'
          '<p id="author">Author font ร่างพระราชบัญญัติ</p>'
          '<p id="mono">Monospace 012345</p>'
          '<div id="editable" contenteditable="true">Editable text ข้อความ</div>'
          '<input id="input" value="Form text ข้อความ"></main>')
PAGE = ('<!doctype html><meta charset="utf-8"><title>Web font isolation</title>' + SAMPLE
        + '<iframe id="blank" src="about:blank"></iframe>'
        + '<iframe id="blank-fragment" src="about:blank#web"></iframe>'
        + '<iframe id="blank-query" src="about:blank?web=1#web"></iframe>'
        + '<iframe id="srcdoc" srcdoc="'
        + html.escape('<!doctype html><meta charset="utf-8">' + SAMPLE, quote=True) + '"></iframe>')
SNAPSHOT = """return {uri:document.documentURI,href:location.href,nodes:Object.fromEntries(
  ['plain','heading','author','mono','editable','input'].map(id=>{
    const node=document.getElementById(id),style=getComputedStyle(node),range=document.createRange();
    range.selectNodeContents(node);
    const bounds=node.localName==='input' ? node.getBoundingClientRect() : range.getBoundingClientRect();
    return [id,{family:style.fontFamily,size:style.fontSize,width:bounds.width,height:bounds.height}]
  }))}"""


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        data = PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def capture(mode, output, firefox, server):
    env = dict(os.environ, MOZ_HEADLESS='1', MOZ_REMOTE_ALLOW_SYSTEM_ACCESS='1')
    lifecycle = Display(output, env)
    process = None
    session = None
    with tempfile.TemporaryDirectory(prefix='tf-web-font-', dir=output) as directory:
        profile = Path(directory)
        if mode != 'baseline':
            shutil.copytree(ROOT / 'chrome', profile / 'chrome',
                            ignore=shutil.ignore_patterns('config.css', '__pycache__', '.omc'))
            config = ':root { --tf-display-horizontal-tabs:block;'
            if mode == 'override':
                config += ' --tf-font-family:Courier; --tf-font-size:31px;'
            (profile / 'chrome/config.css').write_text(config + ' }\n')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        prefs = {'marionette.port': port, 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False, 'sidebar.verticalTabs': False}
        (profile / 'user.js').write_text((ROOT / 'user.js').read_text() + '\n' + ''.join(
            'user_pref(' + json.dumps(key) + ',' + json.dumps(value) + ');\n'
            for key, value in prefs.items()))
        try:
            with (output / f'firefox-{mode}.log').open('w') as log:
                process = subprocess.Popen([firefox, '--headless', '--marionette',
                    '-remote-allow-system-access', '--no-remote', '--new-instance', '--profile',
                    str(profile), 'about:blank'], env=env, stdout=log, stderr=log,
                    **lifecycle.popen_options())
            session = connect(port)
            session.set_context('chrome')
            deadline = time.monotonic() + 30
            while not session.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() > deadline:
                    raise RuntimeError('startup timeout')
                time.sleep(.1)
            identity = session.execute_script("""return {
                profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                pid:Services.appinfo.processID,
                headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless,
                version:Services.appinfo.version} """, sandbox='system')
            if (process.poll() is not None or Path(identity['profile']).resolve() != profile.resolve()
                    or identity['pid'] != process.pid or not identity['headless']
                    or identity['version'] != '160.0'):
                raise RuntimeError(f'unexpected Firefox identity: {identity}')
            ui = session.execute_script("""const style=getComputedStyle(
                gBrowser.selectedTab.querySelector('.tab-label'));
                return {family:style.fontFamily,size:style.fontSize}""", sandbox='system')
            session.set_context('content')
            session.navigate(f'http://127.0.0.1:{server.server_port}/')
            documents = {'http': session.execute_script(SNAPSHOT, sandbox='system')}
            for name in ('blank', 'blank-fragment', 'blank-query', 'srcdoc'):
                session.switch_to_frame(session.find_element('id', name))
                if name.startswith('blank'):
                    session.execute_script('document.body.innerHTML=arguments[0]',
                                           script_args=[SAMPLE], sandbox='system')
                if name == 'srcdoc':
                    session.execute_script("location.hash='web'", sandbox='system')
                documents[name] = session.execute_script(SNAPSHOT, sandbox='system')
                session.switch_to_frame()
            session.navigate('about:preferences#privacy')
            internal = session.execute_script("""return {uri:document.documentURI,
                family:getComputedStyle(document.body).fontFamily}""", sandbox='system')
            return {'identity': identity, 'ui': ui, 'documents': documents, 'internal': internal}
        finally:
            try:
                if session:
                    session.delete_session()
            finally:
                if process:
                    lifecycle.stop_firefox(process)


def compare(results):
    baseline = results['baseline']
    failures = []
    for mode in ('theme', 'override'):
        observed = results[mode]
        for name, reference in baseline['documents'].items():
            actual = observed['documents'][name]
            if (actual['uri'], actual['href']) != (reference['uri'], reference['href']):
                failures.append(f'{mode}:{name} document URL differs')
            for node, expected in reference['nodes'].items():
                found = actual['nodes'][node]
                if found['family'] != expected['family'] or found['size'] != expected['size']:
                    failures.append(f'{mode}:{name}:{node} font differs: {found["family"]} {found["size"]} '
                                    f'vs {expected["family"]} {expected["size"]}')
                if (abs(found['width'] - expected['width']) > 1
                        or abs(found['height'] - expected['height']) > 1):
                    failures.append(f'{mode}:{name}:{node} glyph metrics differ')
    for mode, result in results.items():
        if not result['documents']['http']['uri'].startswith('http://127.0.0.1:'):
            failures.append(f'{mode}:missing HTTP document')
        urls = {'blank': 'about:blank', 'blank-fragment': 'about:blank#web',
                'blank-query': 'about:blank?web=1#web', 'srcdoc': 'about:srcdoc#web'}
        for name, expected in urls.items():
            if (result['documents'][name]['uri'], result['documents'][name]['href']) != (expected, expected):
                failures.append(f'{mode}:{name} unexpected document URL')
            if len(result['documents'][name]['nodes']) != 6:
                failures.append(f'{mode}:{name} incomplete fixture')
        for name, document in result['documents'].items():
            if len(document['nodes']) != 6 or any(node['width'] <= 0 or node['height'] <= 0
                                                  for node in document['nodes'].values()):
                failures.append(f'{mode}:{name} missing text glyph measurements')
        if result['internal']['uri'] != 'about:preferences#privacy':
            failures.append(f'{mode}:missing internal preference document')
    if baseline['documents']['blank']['nodes']['plain']['family'] != 'serif':
        failures.append('baseline:blank default font is not serif')
    if baseline['documents']['blank']['nodes']['author']['family'] != 'sans-serif':
        failures.append('baseline:blank author font is not sans-serif')
    if baseline['documents']['blank']['nodes']['mono']['family'] != 'monospace':
        failures.append('baseline:blank monospace font is not monospace')
    if (results['theme']['ui']['family'] == baseline['ui']['family']
            or results['theme']['internal']['family'] == baseline['internal']['family']):
        failures.append('theme:browser UI and preferences fonts did not change')
    if (results['override']['ui'] != {'family': 'Courier', 'size': '31px'}
            or results['override']['internal']['family'] != 'Courier'):
        failures.append('override:browser UI and preferences fonts did not use Courier')
    return failures


def main():
    parser = argparse.ArgumentParser(description='Check Firefox UI font isolation in web-owned about frames')
    parser.add_argument('--firefox', default='firefox')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        results = {mode: capture(mode, output, args.firefox, server)
                   for mode in ('baseline', 'theme', 'override')}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    failures = compare(results)
    (output / 'results.json').write_text(json.dumps({'runs': results, 'failures': failures}, indent=2) + '\n')
    if failures:
        print('RED:textfoxoverrideswebsiteabout:blankframefonts')
        for failure in failures[:4]:
            print('RED:' + failure)
        raise SystemExit(f'{len(failures)} font isolation checks failed; {output / "results.json"}')
    print(f'PASS: browser UI changed, web fonts and glyph metrics unchanged; {output / "results.json"}')


if __name__ == '__main__':
    main()
