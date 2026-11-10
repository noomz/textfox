#!/usr/bin/env python3
"""Check URL bar result borders in disposable Firefox profiles, Nova on and off."""
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


def check(nova):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-urlbar-') as profile:
        profile = Path(profile)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-accent: rgb(19, 83, 137); --tf-border-width: 3px; }\n')
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
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve() or metadata['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            m.execute_script("gURLBar.focus(); gURLBar.search('mozilla')", sandbox='system')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('#urlbar[open] .urlbarView-results .urlbarView-row')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('URL bar results did not open')
                time.sleep(.2)
            diagnostic = m.execute_script("""
                const url=document.getElementById('urlbar');
                const one=url.querySelector('.urlbarView > .search-one-offs');
                if (!one) return null;
                const selector='#urlbar:not([noresults]) > .urlbarView > .search-one-offs:not([hidden])';
                const measure=()=>({urlNoResults:url.hasAttribute('noresults'), hidden:one.hidden,
                  selectorMatches:one.matches(selector),
                  display:getComputedStyle(one).display, border:getComputedStyle(one).borderTopWidth,
                  style:getComputedStyle(one).borderTopStyle});
                const original=one.hidden;
                if (url.hasAttribute('noresults')) throw new Error('expected populated URL bar');
                one.hidden=false;
                const populated=measure();
                url.setAttribute('noresults','true');
                const empty=measure();
                one.hidden=true;
                const hidden=measure();
                url.removeAttribute('noresults'); one.hidden=original;
                return {populated,empty,hidden};
                """, sandbox='system')
            print(json.dumps({'nova':nova, 'oneOffDiagnostic':diagnostic}), flush=True)
            if (diagnostic is None or not diagnostic['populated']['selectorMatches']
                    or diagnostic['empty']['selectorMatches'] or diagnostic['hidden']['selectorMatches']
                    or diagnostic['populated']['border'] != '3px' or diagnostic['populated']['style'] != 'solid'):
                raise RuntimeError('configured one-offs separator missing with results')
            if diagnostic['empty']['border'] != '0px':
                raise RuntimeError('one-offs separator remains when URL bar has no results')
            if diagnostic['hidden']['border'] != '0px':
                raise RuntimeError('hidden one-offs unexpectedly receive separator')
            result = m.execute_script("""
                const view = document.querySelector('#urlbar[open] .urlbarView');
                const results = document.querySelector('#urlbar[open] > .urlbarView > .urlbarView-results');
                if (!results) throw new Error('URL bar results do not match the themed selector');
                const vs = getComputedStyle(view), rs = getComputedStyle(results);
                const anchor = document.getElementById('urlbar').parentNode;
                const anchorStyle = getComputedStyle(anchor);
                const anchorRect = anchor.getBoundingClientRect();
                return {anchor:{id:anchor.id, urlbarTag:document.getElementById('urlbar').localName,
                      hostPopoverOpen:document.getElementById('urlbar').matches(':popover-open'),
                      hostPopoverAttribute:document.getElementById('urlbar').hasAttribute('popover-open'),
                      panelPopoverOpen:gURLBar.panel.matches(':popover-open'),
                      inlineHeight:anchor.style.getPropertyValue('--urlbar-container-height').trim(),
                      computedHeight:anchorStyle.getPropertyValue('--urlbar-container-height').trim(),
                      measuredHeight:anchorRect.height},
                    rows:results.querySelectorAll('.urlbarView-row').length,
                    resultsBorderTopWidth:rs.borderTopWidth, resultsBorderTopStyle:rs.borderTopStyle,
                    viewBorderTopWidth:vs.borderTopWidth, viewBorderTopStyle:vs.borderTopStyle,
                    viewBorderTopColor:vs.borderTopColor};
                """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'result': result}), flush=True)
            if not result['anchor']['hostPopoverAttribute'] or not result['anchor']['panelPopoverOpen']:
                raise RuntimeError(f'URL bar results popover did not open: {result["anchor"]}')
            if result['resultsBorderTopWidth'] != '0px':
                raise RuntimeError('unexpected result separator border')
            if result['viewBorderTopWidth'] != '3px' or result['viewBorderTopStyle'] != 'solid':
                raise RuntimeError('configured border width lost')
            if result['viewBorderTopColor'] != 'rgb(19, 83, 137)':
                raise RuntimeError('configured accent lost')
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
        check(nova)
