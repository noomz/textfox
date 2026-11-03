#!/usr/bin/env python3
"""Check Nova new-tab layout in real Firefox; run with the live-tool venv Python.

Use a 1280x800 display and run without MOZ_HEADLESS for the reference layout.
The disposable profile enables new-tab feeds and may make network requests.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]

JS = r"""
const wordmark = document.querySelector('.nova-enabled .wordmark');
const search = document.querySelector('.nova-enabled .search-wrapper');
const title = document.querySelector('.nova-enabled .top-site-outer .title');
if (!wordmark || !search || !title) throw new Error('Nova layout elements missing');
const label = title.querySelector('span') || title;
label.textContent = 'Wikipedia';
const rect = e => { const r = e.getBoundingClientRect(); return {x:r.x, y:r.y, width:r.width, height:r.height}; };
const trace = e => {
  const s = getComputedStyle(e);
  return {element:e.localName, class:e.className, rect:rect(e), width:s.width,
    maxWidth:s.maxWidth, overflow:s.overflow, wordBreak:s.wordBreak,
    overflowWrap:s.overflowWrap, whiteSpace:s.whiteSpace, font:s.font};
};
const range = document.createRange();
const measureText = element => {
  range.selectNodeContents(element);
  const lines = [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0)
    .map(r => ({x:r.x, y:r.y, width:r.width, height:r.height}));
  const clips = [{name:'viewport', left:0, top:0, right:innerWidth, bottom:innerHeight}];
  for (let e = element; e; e = e.parentElement) {
    const style = getComputedStyle(e), r = e.getBoundingClientRect();
    const clipX = /^(hidden|clip|scroll|auto)$/.test(style.overflowX);
    const clipY = /^(hidden|clip|scroll|auto)$/.test(style.overflowY);
    if (clipX || clipY) clips.push({name:e.localName + '.' + e.className,
      left:clipX ? r.left + e.clientLeft : -Infinity,
      right:clipX ? r.left + e.clientLeft + e.clientWidth : Infinity,
      top:clipY ? r.top + e.clientTop : -Infinity,
      bottom:clipY ? r.top + e.clientTop + e.clientHeight : Infinity});
  }
  const clipped = [];
  for (const line of lines) for (const clip of clips) {
    if (line.x < clip.left - 1 || line.x + line.width > clip.right + 1 ||
        line.y < clip.top - 1 || line.y + line.height > clip.bottom + 1)
      clipped.push(clip.name);
  }
  return {lines, clipped:[...new Set(clipped)]};
};
const shortcut = measureText(label);
const fixture = document.createElement('div');
Object.assign(fixture.style, {position:'fixed', left:'0', top:'0', width:'10px',
  height:'20px', overflow:'hidden', whiteSpace:'nowrap'});
fixture.textContent = 'A deliberately clipped label';
document.body.appendChild(fixture);
const clippingSelfTest = measureText(fixture);
fixture.remove();
const ancestors = [];
for (let e = label; e && ancestors.length < 5; e = e.parentElement) ancestors.push(trace(e));
const pseudo = getComputedStyle(wordmark, '::before');
let logo = {content:pseudo.content, wordmark:trace(wordmark), search:trace(search), estimatedLines:[]};
if (pseudo.content !== 'none' && pseudo.content !== 'normal') {
  const clone = document.createElement('span');
  clone.textContent = pseudo.content.slice(1, -1).replace(/\\a /g, '\n').replace(/\\\\/g, '\\').replace(/\\"/g, '"');
  Object.assign(clone.style, {position:'absolute', left:'0', top:'0', visibility:'hidden',
    whiteSpace:pseudo.whiteSpace, font:pseudo.font, lineHeight:pseudo.lineHeight, display:'block'});
  document.body.appendChild(clone);
  range.selectNodeContents(clone);
  const origin = clone.getBoundingClientRect(), w = wordmark.getBoundingClientRect();
  logo.estimatedLines = [...range.getClientRects()].filter(r => r.width > 0)
    .map(r => ({x:w.x + r.x - origin.x, y:w.y + r.y - origin.y, width:r.width, height:r.height}));
  clone.remove();
}
return {viewport:[innerWidth, innerHeight], logo,
  shortcut:{fixture:'Wikipedia', ...shortcut, ancestors}, clippingSelfTest};
"""


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-nova-layout-') as profile:
        shutil.copytree(ROOT / 'chrome', Path(profile) / 'chrome')
        prefs = {
            'toolkit.legacyUserProfileCustomizations.stylesheets': True,
            'browser.nova.enabled': True,
            'browser.newtabpage.activity-stream.nova.enabled': True,
            'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
            'browser.newtabpage.activity-stream.widgets.enabled': True,
            'browser.shell.checkDefaultBrowser': False,
            'marionette.port': port,
        }
        (Path(profile) / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', profile, 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, buildID:Services.appinfo.appBuildID, browserNova:Services.prefs.getBoolPref('browser.nova.enabled'), newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(metadata['profile']).resolve() != Path(profile).resolve():
                raise RuntimeError('Marionette connected to an unexpected Firefox profile')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .top-site-outer .title")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Nova new-tab shortcut did not initialize')
                time.sleep(.25)
            time.sleep(2)
            result = m.execute_script(JS)
            result['firefox'] = metadata
            print(json.dumps(result, indent=2), flush=True)
            if not metadata['browserNova'] or not metadata['newtabNova']:
                raise RuntimeError('Nova preferences not enabled')
            logo = result['logo']
            search = logo['search']['rect']
            overlap = any(r['x'] < search['x'] + search['width'] and r['x'] + r['width'] > search['x'] and r['y'] < search['y'] + search['height'] and r['y'] + r['height'] > search['y'] for r in logo['estimatedLines'])
            failures = []
            if not logo['estimatedLines']:
                failures.append('logo has no measurable lines')
            if overlap:
                failures.append('estimated logo line box overlaps search wrapper')
            if not result['clippingSelfTest']['lines'] or not result['clippingSelfTest']['clipped']:
                raise RuntimeError('clipping check failed its deliberately clipped fixture')
            if not result['shortcut']['lines']:
                failures.append('Wikipedia shortcut has no measurable text')
            if result['shortcut']['clipped']:
                failures.append('Wikipedia shortcut text clipped by ' + ', '.join(result['shortcut']['clipped']))
            if len({round(r['y'], 1) for r in result['shortcut']['lines']}) != 1:
                failures.append('Wikipedia shortcut wraps')
            if failures:
                print('FAIL: ' + '; '.join(failures), file=sys.stderr)
                return 1
            print('INCONCLUSIVE: no detected overlap, wrapping, or clipping; visibility and painted pixels not verified')
            return 2
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
    sys.exit(main())
