#!/usr/bin/env python3
"""Check toolbar URL bar geometry and borders, with and without Nova."""
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


FIELD_GEOMETRY = """
    const field = document.querySelector('#nav-bar #urlbar > .urlbar-background');
    const input = document.querySelector('#nav-bar #urlbar > .urlbar-input-container');
    const nav = document.getElementById('nav-bar');
    const rect = element => {
        const {left, top, right, bottom} = element.getBoundingClientRect();
        return {left, top, right, bottom};
    };
    const corners = element => {
        const s = getComputedStyle(element);
        return [s.borderTopLeftRadius, s.borderTopRightRadius,
                s.borderBottomLeftRadius, s.borderBottomRightRadius];
    };
    const sampleField = () => {
        const s = getComputedStyle(field);
        const bounds = rect(field), bar = rect(nav), ns = getComputedStyle(nav);
        const outlineOutset = Math.max(0, parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset));
        return {bounds, input:rect(input), corners:corners(field), outline:s.outlineStyle,
                gapTop:bounds.top - bar.top - parseFloat(ns.borderTopWidth) - outlineOutset,
                gapBottom:bar.bottom - parseFloat(ns.borderBottomWidth) - bounds.bottom - outlineOutset};
    };
"""


def check(nova, radius_override):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-urlbar-') as profile:
        profile = Path(profile)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        radius = f'--tf-urlbar-radius: {radius_override};' if radius_override is not None else ''
        (profile / 'chrome' / 'config.css').write_text(
            ':root { --tf-accent: rgb(19, 83, 137); --tf-border-width: 3px; '
            f'--tf-text-transform: uppercase; {radius} }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.verticalTabs': nova, 'sidebar.revamp': nova,
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
            placeholder = m.execute_script("""
                const input=document.getElementById('urlbar-input'),root=document.documentElement;
                const s=root.style,old=s.getPropertyValue('--tf-text-transform'),priority=s.getPropertyPriority('--tf-text-transform');
                const sample=()=>({placeholder:input.getAttribute('placeholder'),shown:input.matches(':placeholder-shown'),
                  transform:getComputedStyle(input,'::placeholder').textTransform});
                const before=sample();
                try{s.setProperty('--tf-text-transform','lowercase','important');return {before,control:sample()};}
                finally{if(old)s.setProperty('--tf-text-transform',old,priority);else s.removeProperty('--tf-text-transform');}
                """,sandbox='system')
            if placeholder['before']['transform']!='uppercase' or placeholder['control']['transform']!='lowercase':
                raise RuntimeError(f'URL bar placeholder token control failed: {placeholder}')
            print(json.dumps({'firefox':metadata,'placeholder':placeholder}),flush=True)
            idle = m.execute_script(FIELD_GEOMETRY + """
                if (gURLBar.focused || gURLBar.panel.matches(':popover-open')) {
                    throw new Error('expected the toolbar address field to be idle');
                }
                return sampleField();
                """, sandbox='system')
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
            result = m.execute_script(FIELD_GEOMETRY + """
                const view = document.querySelector('#nav-bar #urlbar > .urlbarView:popover-open');
                const results = view?.querySelector(':scope > .urlbarView-results');
                if (!results || !gURLBar.focused) throw new Error('expected focused URL bar with open results');
                const vs = getComputedStyle(view), rs = getComputedStyle(results);
                const focused = sampleField(), panel = rect(view);
                return {focused, panel:{left:panel.left-focused.bounds.left,
                        right:panel.right-focused.bounds.right, top:panel.top-rect(nav).bottom,
                        corners:corners(view)},
                    rows:results.querySelectorAll('.urlbarView-row').length,
                    resultsBorderTopWidth:rs.borderTopWidth, resultsBorderTopStyle:rs.borderTopStyle,
                    viewBorderTopWidth:vs.borderTopWidth, viewBorderTopStyle:vs.borderTopStyle,
                    viewBorderTopColor:vs.borderTopColor};
                """, sandbox='system')
            print(json.dumps({'nova': nova, 'radiusOverride': radius_override,
                              'idle': idle, 'result': result}), flush=True)
            focused, panel = result['focused'], result['panel']
            if min(idle['gapTop'], idle['gapBottom'], focused['gapTop'], focused['gapBottom']) <= 0:
                raise RuntimeError(f'URL field is not inset from the navbar borders: {idle}, {focused}')
            for box in ('bounds', 'input'):
                if any(abs(idle[box][edge] - focused[box][edge]) > 0.5 for edge in idle[box]):
                    raise RuntimeError(f'URL field geometry changed on open: {box} {idle[box]} -> {focused[box]}')
            if focused['outline'] != 'solid':
                raise RuntimeError('URL field lost its focus ring')
            expected_corners = [radius_override or '6px'] * 4
            if any(box['corners'] != expected_corners for box in (idle, focused, panel)):
                raise RuntimeError(f'URL field and results must share {expected_corners[0]} corners')
            if abs(panel['left']) > 0.5 or abs(panel['right']) > 0.5 or panel['top'] < -0.5:
                raise RuntimeError(f'URL results are misaligned or cover the navbar: {panel}')
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
        for radius_override in (None, '0px'):
            check(nova, radius_override)
