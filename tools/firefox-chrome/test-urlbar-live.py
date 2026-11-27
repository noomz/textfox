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
        const outlineOutset = s.outlineStyle === 'none' ? 0 :
            Math.max(0, parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset));
        return {bounds, input:rect(input), corners:corners(field), outline:s.outlineStyle,
                outlineOffset:s.outlineOffset,
                gapTop:bounds.top - bar.top - parseFloat(ns.borderTopWidth) - outlineOutset,
                gapBottom:bar.bottom - parseFloat(ns.borderBottomWidth) - bounds.bottom - outlineOutset};
    };
"""


def check(nova, radius_override, border_width, accent, outline_offset):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-urlbar-') as profile:
        profile = Path(profile)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        radius = f'--tf-urlbar-radius: {radius_override};' if radius_override is not None else ''
        (profile / 'chrome' / 'config.css').write_text(
            f':root {{ --tf-accent: rgb({", ".join(map(str, accent))}); --tf-border-width: {border_width}px; '
            '--tf-border: rgb(203, 39, 107); --tf-urlbar-bg: rgb(239, 243, 247); '
            f'--tf-text-transform: uppercase; {radius} }}\n'
            f'#urlbar[focused] > .urlbar-background {{ outline-offset: {outline_offset}px !important; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova, 'layout.css.devPixelsPerPx': '1',
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
            focused = m.execute_script(FIELD_GEOMETRY + """
                gURLBar.focus();
                if (!gURLBar.focused || gURLBar.panel.matches(':popover-open')) {
                    throw new Error('expected a focused address field without results');
                }
                return sampleField();
                """, sandbox='system')
            m.execute_script("gURLBar.search('mozilla')", sandbox='system')
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
                    or diagnostic['populated']['border'] != f'{border_width}px'
                    or diagnostic['populated']['style'] != 'solid'):
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
                const open = sampleField(), panel = rect(view);
                return {open, panel, corners:corners(view),
                    rows:results.querySelectorAll('.urlbarView-row').length,
                    resultsBorderTopWidth:rs.borderTopWidth,
                    frameWidths:[vs.borderLeftWidth, vs.borderRightWidth, vs.borderBottomWidth]};
                """, sandbox='system')
            print(json.dumps({'nova': nova, 'radiusOverride': radius_override, 'outlineOffset': outline_offset,
                              'idle': idle, 'focused': focused, 'result': result}), flush=True)
            opened, panel = result['open'], result['panel']
            if min(idle['gapTop'], idle['gapBottom'], focused['gapTop'], focused['gapBottom'],
                   opened['gapTop'], opened['gapBottom']) <= 0:
                raise RuntimeError('URL field is not inset from the navbar borders')
            for state in (focused, opened):
                for box in ('bounds', 'input'):
                    if any(abs(idle[box][edge] - state[box][edge]) > 0.5 for edge in idle[box]):
                        raise RuntimeError(f'URL field geometry changed: {box} {idle[box]} -> {state[box]}')
            if focused['outline'] != 'solid' or focused['outlineOffset'] != f'{outline_offset}px':
                raise RuntimeError(f'expected a standalone focus ring with offset {outline_offset}px: {focused}')
            field = opened['bounds']
            if (abs(panel['left'] - field['left']) > 0.5 or abs(panel['right'] - field['right']) > 0.5
                    or abs(panel['top'] - field['bottom']) > 0.5):
                raise RuntimeError(f'URL results must attach flush to the field: {field}, {panel}')
            radius = radius_override or '6px'
            if (any(box['corners'] != [radius] * 4 for box in (idle, focused))
                    or opened['corners'] != [radius, radius, '0px', '0px']
                    or result['corners'] != ['0px', '0px', radius, radius]):
                raise RuntimeError(f'URL field and results must round only the outer corners when attached ({radius})')
            if result['resultsBorderTopWidth'] != '0px':
                raise RuntimeError('unexpected extra results border')
            if result['frameWidths'] != [f'{border_width}px'] * 3:
                raise RuntimeError('configured frame width lost')
            paint = m.execute_script(FIELD_GEOMETRY + """
                if (devicePixelRatio !== 1) throw new Error('expected 1 device pixel per CSS pixel');
                const {left, right, bottom} = rect(field);
                const joint = Math.round(bottom);
                const toolbarEdge = Math.round(rect(nav).bottom) - 1;
                const x = Math.floor(left) - 1, y = joint - 3;
                const canvas = document.createElementNS('http://www.w3.org/1999/xhtml', 'canvas');
                canvas.width = Math.ceil(right) - x + 2;
                canvas.height = toolbarEdge - y + 1;
                const ctx = canvas.getContext('2d');
                const capture = () => {
                    ctx.drawWindow(window, x, y, canvas.width, canvas.height, 'white', ctx.DRAWWINDOW_DRAW_VIEW);
                    return ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                };
                const pixel = (data, col, row) => {
                    const offset = ((row - y) * canvas.width + col) * 4;
                    return Array.from(data.subarray(offset, offset + 3)).join(',');
                };
                const frame = arguments[0].join(',');
                const toolbar = '203,39,107';
                const data = capture(), middle = Math.floor((left + right) / 2) - x;
                const columns = (row, color) => Array.from({length:canvas.width}, (_, i) => i)
                    .filter(col => pixel(data, col, row) === color);
                const toolbarOutside = pixel(data, 0, toolbarEdge);
                const toolbarInside = columns(toolbarEdge, toolbar).filter(col => col + x > left && col + x < right - 1);
                gURLBar.panel.style.setProperty('opacity', '0', 'important');
                const uncoveredToolbar = pixel(capture(), middle, toolbarEdge);
                gURLBar.panel.style.removeProperty('opacity');
                return {joint: [-2, -1, 0, 1, 2].map(delta => pixel(data, middle, joint + delta)),
                    toolbarOutside, toolbarInside, uncoveredToolbar,
                    sidesAbove:columns(joint - 1, frame), sidesBelow:columns(joint + 1, frame)};
                """, script_args=[accent], sandbox='system')
            print(json.dumps({'paint':paint}), flush=True)
            before, last, divider, *after = paint['joint']
            frame = ','.join(map(str, accent))
            if before != last or last == frame or divider != frame or after != ['239,243,247'] * 2:
                raise RuntimeError(f'expected exactly one accent pixel through the joint: {paint["joint"]}')
            if paint['toolbarOutside'] != '203,39,107' or paint['uncoveredToolbar'] != '203,39,107':
                raise RuntimeError('toolbar border did not paint in the control')
            if paint['toolbarInside']:
                raise RuntimeError(f'popup did not cover the toolbar border: {paint["toolbarInside"]}')
            if paint['sidesAbove'] != paint['sidesBelow'] or len(paint['sidesAbove']) != 2 * border_width:
                raise RuntimeError(f'painted side borders do not align: {paint["sidesAbove"]}, {paint["sidesBelow"]}')
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
        check(nova, None, 3, (19, 83, 137), -2)
        check(nova, '0px', 5, (107, 48, 145), 2)
