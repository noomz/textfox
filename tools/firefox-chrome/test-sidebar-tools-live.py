#!/usr/bin/env python3
"""Check the sidebar tools visibility option in disposable Firefox profiles."""
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


def check(nova, display, vertical):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-sidebar-tools-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        (profile / 'chrome' / 'config.css').write_text(f':root {{ --tf-display-sidebar-tools: {display}; }}\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.revamp': True, 'sidebar.verticalTabs': vertical,
                 'sidebar.visibility': 'always-show',
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
            metadata = m.execute_script("return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, build:Services.appinfo.appBuildID, nova:Services.prefs.getBoolPref('browser.nova.enabled'), verticalTabs:Services.prefs.getBoolPref('sidebar.verticalTabs'), revamp:Services.prefs.getBoolPref('sidebar.revamp')}", sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['verticalTabs'] != vertical or not metadata['revamp']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            ready = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                SidebarController.promiseInitialized.then(() => SidebarController.updateUIState({hidden:false, launcherExpanded:true}))
                  .then(() => done(true), e => done(String(e)));
                """, sandbox='system')
            if ready is not True:
                raise RuntimeError(f'sidebar state initialization failed: {ready}')
            deadline = time.monotonic() + 20
            while not m.execute_script("return !!document.querySelector('sidebar-main')?.shadowRoot?.querySelector('.buttons-wrapper')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('sidebar tools did not initialize')
                time.sleep(.2)
            result = m.execute_script("""
                const host = document.querySelector('sidebar-main');
                const wrapper = host.shadowRoot.querySelector('.buttons-wrapper');
                const s = getComputedStyle(wrapper), rect = wrapper.getBoundingClientRect();
                const splitter = host.shadowRoot.querySelector('#sidebar-tools-and-extensions-splitter');
                const ss = splitter && getComputedStyle(splitter);
                return {rootOption:getComputedStyle(document.documentElement).getPropertyValue('--tf-display-sidebar-tools').trim(),
                        splitter:splitter && {display:ss.display, width:splitter.getBoundingClientRect().width,
                          height:splitter.getBoundingClientRect().height, html:splitter.outerHTML},
                        display:s.display, visibility:s.visibility, width:rect.width, height:rect.height,
                        part:wrapper.getAttribute('part'), hostHidden:host.hidden,
                        lightDomMatches:document.querySelectorAll('.buttons-wrapper').length,
                        buttons:wrapper.querySelectorAll('moz-button').length};
                """, sandbox='system')
            if result['rootOption'] != display:
                raise RuntimeError('requested sidebar option not applied')
            print(json.dumps({'firefox': metadata, 'requested': display, 'result': result}), flush=True)
            if result['hostHidden'] or result['buttons'] == 0:
                raise RuntimeError('sidebar tools fixture is not available')
            if result['display'] != display:
                raise RuntimeError('sidebar tools display does not follow configuration')
            if display == 'flex' and (result['visibility'] != 'visible' or result['width'] <= 0 or result['height'] <= 0):
                raise RuntimeError('configured sidebar tools are not visible')
            if display == 'none' and (result['width'] != 0 or result['height'] != 0):
                raise RuntimeError('hidden sidebar tools still occupy space')
            if vertical and not result['splitter']:
                raise RuntimeError('sidebar tools splitter fixture is missing')
            if vertical and display == 'flex' and (result['splitter']['display'] == 'none' or result['splitter']['height'] <= 0):
                raise RuntimeError('visible sidebar tools have no resize splitter')
            if vertical and display == 'none' and result['splitter']['display'] != 'none':
                raise RuntimeError('hidden sidebar tools still show resize splitter')
            if vertical and display == 'flex':
                collapsed = m.execute_async_script("""
                    const done = arguments[arguments.length - 1];
                    SidebarController.updateUIState({launcherExpanded:false}).then(() => done(true), e => done(String(e)));
                    """, sandbox='system')
                if collapsed is not True:
                    raise RuntimeError(f'could not collapse sidebar launcher: {collapsed}')
                drag = m.execute_script("""
                    const host = document.querySelector('sidebar-main');
                    const splitter = host.shadowRoot.querySelector('#sidebar-tools-and-extensions-splitter');
                    const r = splitter.getBoundingClientRect();
                    return {x:r.x + r.width / 2, y:r.y + r.height / 2,
                            expanded:host.hasAttribute('expanded'),
                            height:host.shadowRoot.querySelector('.buttons-wrapper').getBoundingClientRect().height};
                    """, sandbox='system')
                if drag['expanded'] or drag['height'] <= 0:
                    raise RuntimeError(f'collapsed tools fixture invalid: {drag}')
                vertical_strip = m.execute_script("""
                    const strip = document.querySelector('box#vertical-tabs');
                    if (!strip) throw new Error('vertical tab strip selector did not match');
                    const style = getComputedStyle(strip), rect = strip.getBoundingClientRect();
                    return {display:style.display, border:style.borderTopWidth,
                            width:rect.width, height:rect.height};
                    """, sandbox='system')
                print(json.dumps({'nova': nova, 'verticalStrip': vertical_strip}), flush=True)
                if (vertical_strip['display'] == 'none' or vertical_strip['border'] != '2px'
                        or vertical_strip['width'] <= 0 or vertical_strip['height'] <= 0):
                    raise RuntimeError(f'vertical tab strip is not styled or visible: {vertical_strip}')
                m.execute_script('gBrowser.pinTab(gBrowser.addTrustedTab("about:blank"))', sandbox='system')
                pinned = m.execute_script("""
                    const tabs = document.getElementById('tabbrowser-tabs');
                    const p = document.getElementById('pinned-tabs-container');
                    const r = p.getBoundingClientRect(), t = tabs.getBoundingClientRect();
                    return {orient:p.getAttribute('orient'), display:getComputedStyle(p).display,
                            pinnedWidth:r.width, tabsWidth:t.width, expanded:tabs.hasAttribute('expanded'),
                            pinnedTabs:gBrowser.tabs.filter(tab => tab.pinned).length};
                    """, sandbox='system')
                print(json.dumps({'nova': nova, 'pinned': pinned}), flush=True)
                if (pinned['orient'] != 'vertical' or pinned['expanded'] or pinned['display'] == 'none'
                        or pinned['pinnedTabs'] < 1 or pinned['pinnedWidth'] <= 0
                        or abs(pinned['pinnedWidth'] - pinned['tabsWidth']) > 1):
                    raise RuntimeError('collapsed pinned tabs do not fill the tab strip')
                actions = m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'})
                actions.pointer_move(round(drag['x']), round(drag['y'])).pointer_down()
                actions.pointer_move(round(drag['x']), round(drag['y'] + 100), duration=500).pointer_up().perform()
                after = m.execute_script("return document.querySelector('sidebar-main').shadowRoot.querySelector('.buttons-wrapper').getBoundingClientRect().height", sandbox='system')
                print(json.dumps({'nova': nova, 'toolsHeightBefore': drag['height'], 'toolsHeightAfter': after}), flush=True)
                if after >= drag['height']:
                    raise RuntimeError('dragging the sidebar tools splitter did not shrink the tools area')
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
        for display in ('flex', 'none'):
            for vertical in (False, True):
                check(nova, display, vertical)
