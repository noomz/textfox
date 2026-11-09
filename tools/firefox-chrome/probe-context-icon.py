#!/usr/bin/env python3
"""Inspect link context-menu icon preference in disposable Firefox profiles."""
import argparse
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


def check_context(m, selector, expected_id):
    m.set_context('content')
    target = m.find_element('css selector', selector)
    m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(target, button=2).perform()
    m.set_context('chrome')
    deadline = time.monotonic() + 15
    while True:
        result = m.execute_script("""
            const popup=document.getElementById('contentAreaContextMenu');
            const expected=popup.querySelector(`#${arguments[0]}`);
            if (popup.state !== 'open' || !expected || expected.hidden) return null;
            const r=expected.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return null;
            return {menuItems:[...popup.querySelectorAll('menuitem[id],menu[id]')].map(e=>{
              const s=getComputedStyle(e),rect=e.getBoundingClientRect();
              return {id:e.id,hidden:e.hidden,display:s.display,visibility:s.visibility,
                image:s.backgroundImage,width:rect.width,height:rect.height};
            })};
            """, script_args=[expected_id], sandbox='system')
        if result:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f'context fixture {selector} did not initialize: {result}')
        time.sleep(.1)
    m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
    deadline = time.monotonic() + 10
    while m.execute_script("return document.getElementById('contentAreaContextMenu').state", sandbox='system') != 'closed':
        if time.monotonic() >= deadline:
            raise RuntimeError(f'context fixture {selector} did not close')
        time.sleep(.1)
    return result


def check_pdf(m, profile, enabled):
    source = profile / 'textfox-probe.ps'
    pdf = profile / 'textfox-probe.pdf'
    source.write_text('%!PS\n/Helvetica findfont 24 scalefont setfont\n100 700 moveto (Textfox PDF fixture) show\nshowpage\n')
    subprocess.run(['ps2pdf', str(source), str(pdf)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    m.set_context('content')
    m.navigate(pdf.as_uri())
    deadline = time.monotonic() + 30
    while not m.execute_script("return !!document.querySelector('#editorFreeTextButton') && !!document.querySelector('.page .textLayer')"):
        if time.monotonic() >= deadline:
            raise RuntimeError('PDF viewer did not initialize')
        time.sleep(.2)
    m.find_element('id', 'editorFreeTextButton').click()
    deadline = time.monotonic() + 15
    while not m.execute_script("return !!document.querySelector('.annotationEditorLayer.freetextEditing')"):
        if time.monotonic() >= deadline:
            raise RuntimeError('PDF free-text mode did not initialize')
        time.sleep(.1)
    layer = m.find_element('css selector', '.annotationEditorLayer.freetextEditing')
    m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(layer).perform()
    deadline = time.monotonic() + 15
    while not m.execute_script("return !!document.querySelector('.freeTextEditor [contenteditable]')"):
        if time.monotonic() >= deadline:
            raise RuntimeError('PDF text editor did not initialize')
        time.sleep(.1)
    m.actions.sequence('key', 'keyboard').send_keys('textfox probe').perform()
    deadline = time.monotonic() + 10
    while not m.execute_script("return document.querySelector('.freeTextEditor [contenteditable]')?.textContent === 'textfox probe'"):
        if time.monotonic() >= deadline:
            raise RuntimeError('PDF editor did not receive fixture text')
        time.sleep(.1)
    m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
    editor = m.find_element('css selector', '.freeTextEditor')
    m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(editor).perform()
    point = m.execute_script("const e=document.querySelector('.freeTextEditor'), r=e.getBoundingClientRect(); return {x:r.x+1,y:r.y+1,selected:e.classList.contains('selectedEditor')}")
    if not point['selected']:
        raise RuntimeError(f'PDF editor was not selected: {point}')
    m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).pointer_move(
        round(point['x']), round(point['y'])).click(button=2).perform()
    m.set_context('chrome')
    deadline = time.monotonic() + 15
    while True:
        result = m.execute_script("""
            const menu=document.getElementById('contentAreaContextMenu');
            const item=menu.querySelector('#context-pdfjs-select-all');
            if (menu.state !== 'open' || !item || item.hidden) return null;
            const s=getComputedStyle(item),r=item.getBoundingClientRect();
            const menuItems=[...menu.querySelectorAll('menuitem[id],menu[id]')].map(e=>{
              const st=getComputedStyle(e),rect=e.getBoundingClientRect();
              return {id:e.id,hidden:e.hidden,display:st.display,visibility:st.visibility,
                image:st.backgroundImage,width:rect.width,height:rect.height};
            });
            return {hidden:item.hidden,disabled:item.disabled,display:s.display,image:s.backgroundImage,
              width:r.width,height:r.height,legacyMatches:menu.querySelectorAll('#context-pdfjs-selectall').length,
              menuItems};
            """, sandbox='system')
        if result and result['width'] > 0 and result['height'] > 0:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f'PDF editor context menu did not initialize: {result}')
        time.sleep(.1)
    if result['disabled']:
        raise RuntimeError(f'PDF select-all item is disabled: {result}')
    result['control'] = m.execute_script("""
        const css='@media -moz-pref("shyfox.enable.context.menu.icons") { #contentAreaContextMenu #context-pdfjs-select-all { background-image: url("chrome://browser/skin/privateBrowsing.svg") !important; } }';
        const uri='data:text/css,'+encodeURIComponent(css);
        window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
        try { return getComputedStyle(document.getElementById('context-pdfjs-select-all')).backgroundImage; }
        finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
        """, sandbox='system')
    expected = 'url("chrome://browser/skin/privateBrowsing.svg")' if enabled else 'none'
    if result['control'] != expected:
        raise RuntimeError(f'PDF menu selector intervention failed: {result}')
    m.execute_script("document.getElementById('context-pdfjs-select-all').addEventListener('command', e => e.currentTarget.setAttribute('data-textfox-command','true'), {once:true})", sandbox='system')
    item = m.find_element('id', 'context-pdfjs-select-all')
    m.actions.sequence('pointer', 'mouse', {'pointerType':'mouse'}).click(item).perform()
    deadline = time.monotonic() + 10
    while not m.execute_script("return document.getElementById('contentAreaContextMenu').state === 'closed' && document.getElementById('context-pdfjs-select-all').getAttribute('data-textfox-command') === 'true'", sandbox='system'):
        if time.monotonic() >= deadline:
            diagnostic = m.execute_script("const e=document.getElementById('context-pdfjs-select-all'); return {state:document.getElementById('contentAreaContextMenu').state,command:e.getAttribute('data-textfox-command'),hidden:e.hidden,disabled:e.disabled}", sandbox='system')
            raise RuntimeError(f'PDF select-all activation did not dispatch and close menu: {diagnostic}')
        time.sleep(.1)
    m.set_context('content')
    result['selectedEditors'] = m.execute_script("return document.querySelectorAll('.freeTextEditor.selectedEditor').length")
    if result['selectedEditors'] < 1:
        raise RuntimeError(f'PDF select-all left no editor selected: {result}')
    m.set_context('chrome')
    return result


def check(nova, enabled, pdf):
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
                 'browser.shell.checkDefaultBrowser': False,
                 'pdfjs.annotationEditorMode': 0}
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
            m.navigate('data:text/html,<a href="https://example.invalid/">textfox link fixture</a><p id="page">textfox page fixture</p><input id="edit" value="textfox edit fixture">')
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
                    const menuItems=[...popup.querySelectorAll('menuitem[id],menu[id]')].map(e=>{
                      const s=getComputedStyle(e),r=e.getBoundingClientRect();
                      return {id:e.id,hidden:e.hidden,display:s.display,visibility:s.visibility,image:s.backgroundImage,
                        width:r.width,height:r.height};
                    });
                    return {state:popup.state, hidden:item.hidden, image:style.backgroundImage, menuItems,
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
            other_contexts = {'page':check_context(m, '#page', 'context-savepage'),
                              'input':check_context(m, '#edit', 'context-selectall')}
            pdf_result = check_pdf(m, profile, enabled) if pdf else None
            print(json.dumps({'firefox':metadata, 'item':state, 'otherContexts':other_contexts,
                              'extensionMedia':extension_media, 'modernIntervention':intervention,
                              'pdfSelectorControl':pdf_control, 'pdfEditor':pdf_result}), flush=True)
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', action='store_true', help='also generate a PDF using ps2pdf and inspect its editor menu')
    args = parser.parse_args()
    if args.pdf and not shutil.which('ps2pdf'):
        parser.error('--pdf requires ps2pdf')
    for nova in (False, True):
        for enabled in (False, True):
            check(nova, enabled, args.pdf)
