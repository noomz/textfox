#!/usr/bin/env python3
"""Inspect link context-menu icon preference in disposable Firefox profiles."""
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


def check(nova, enabled):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-context-icon-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'shyfox.enable.context.menu.icons': enabled,
                 'shyfox.enable.ext.mono.context.icons': enabled,
                 'shyfox.enable.ext.mono.toolbar.icons': enabled,
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
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref('shyfox.enable.context.menu.icons'),
                  extensionContextPref:Services.prefs.getBoolPref('shyfox.enable.ext.mono.context.icons'),
                  extensionToolbarPref:Services.prefs.getBoolPref('shyfox.enable.ext.mono.toolbar.icons')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['iconPref'] != enabled
                    or metadata['extensionContextPref'] != enabled or metadata['extensionToolbarPref'] != enabled):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.set_context('content')
            m.navigate('data:text/html,<a href="https://example.invalid/">textfox link fixture</a>')
            link = m.find_element('css selector', 'a')
            m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(link, button=2).perform()
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while True:
                state = m.execute_script("""
                    const popup=document.getElementById('contentAreaContextMenu');
                    const item=document.getElementById('context-openlinkprivate');
                    if (popup.state !== 'open' || !item || item.hidden) return null;
                    const style=getComputedStyle(item), rect=item.getBoundingClientRect();
                    const legacyPdf=popup.querySelectorAll('#context-pdfjs-selectall');
                    const currentPdf=popup.querySelectorAll('#context-pdfjs-select-all');
                    return {state:popup.state, hidden:item.hidden, image:style.backgroundImage,
                      pdfSelectAll:{legacyCount:legacyPdf.length,currentCount:currentPdf.length,
                        currentHidden:currentPdf[0]?.hidden},
                      legacyMedia:matchMedia('(-moz-bool-pref: "shyfox.enable.context.menu.icons")').matches,
                      modernMedia:matchMedia('-moz-pref("shyfox.enable.context.menu.icons")').matches,
                      display:style.display, width:rect.width, height:rect.height};
                    """, sandbox='system')
                if state and state['width'] > 0 and state['height'] > 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'link context menu did not initialize: {state}')
                time.sleep(.1)
            extension_media = m.execute_script("""
                return Object.fromEntries(['shyfox.enable.ext.mono.context.icons', 'shyfox.enable.ext.mono.toolbar.icons']
                  .map(pref => [pref, {legacy:matchMedia(`(-moz-bool-pref: "${pref}")`).matches,
                    modern:matchMedia(`-moz-pref("${pref}")`).matches}]));
                """, sandbox='system')
            if any(value['modern'] != enabled for value in extension_media.values()):
                raise RuntimeError(f'extension icon preference media control failed: {extension_media}')
            intervention = m.execute_script("""
                const css = '@media -moz-pref("shyfox.enable.context.menu.icons") { #contentAreaContextMenu #context-openlinkprivate { background-image: url("chrome://browser/skin/privateBrowsing.svg") !important; } }';
                const uri='data:text/css,' + encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try {
                  return getComputedStyle(document.getElementById('context-openlinkprivate')).backgroundImage;
                } finally {
                  window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                }
                """, sandbox='system')
            pdf_control = m.execute_script("""
                const item=document.getElementById('context-pdfjs-select-all');
                if (!item) return null;
                const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #contentAreaContextMenu #context-pdfjs-select-all { background-image: url("chrome://browser/skin/privateBrowsing.svg") !important; } }';
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(item).backgroundImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                """, sandbox='system')
            expected = 'url("chrome://browser/skin/privateBrowsing.svg")' if enabled else 'none'
            if state['modernMedia'] != enabled or intervention != expected or pdf_control != expected:
                raise RuntimeError(f'modern preference intervention was inconclusive: {state}, {intervention}, {pdf_control}')
            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
            deadline = time.monotonic() + 10
            while m.execute_script("return document.getElementById('contentAreaContextMenu').state", sandbox='system') != 'closed':
                if time.monotonic() >= deadline:
                    raise RuntimeError('Escape did not close context menu')
                time.sleep(.1)
            print(json.dumps({'firefox':metadata, 'item':state, 'extensionMedia':extension_media,
                              'modernIntervention':intervention, 'pdfSelectorControl':pdf_control}), flush=True)
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
        for enabled in (False, True):
            check(nova, enabled)
