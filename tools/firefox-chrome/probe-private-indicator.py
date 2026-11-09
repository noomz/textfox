#!/usr/bin/env python3
"""Compare private-window indicator geometry with vanilla and textfox profiles."""
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


def check(nova, themed, vertical):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-private-indicator-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'sidebar.verticalTabs': vertical,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile),
                                    '--private-window', 'about:privatebrowsing'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  vertical:Services.prefs.getBoolPref('sidebar.verticalTabs'),
                  private:PrivateBrowsingUtils.isWindowPrivate(window)};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['vertical'] != vertical or not metadata['private']):
                raise RuntimeError('unexpected Firefox process, profile, window, or preferences')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('private window startup did not finish')
                time.sleep(.1)
            result = m.execute_script("""
                const toolbox = document.getElementById('navigator-toolbox');
                const nodes = [...document.querySelectorAll('.private-browsing-indicator-with-label, .private-browsing-indicator-button')];
                const privateSelector=':root[privatebrowsingmode]:not([firefoxviewhidden]) :is(toolbarbutton:not(#firefox-view-button), toolbarpaletteitem:not(#wrapper-firefox-view-button)) + #tabbrowser-tabs';
                const tabs=document.getElementById('tabbrowser-tabs'), previous=tabs.previousElementSibling;
                const root=document.documentElement, before=document.querySelectorAll(privateSelector).length;
                const control=document.createXULElement('toolbarbutton');
                tabs.before(control);
                const controlMatches=document.querySelectorAll(privateSelector).length;
                control.remove();
                return {tabsHidden:toolbox.hasAttribute('tabs-hidden'),
                  privateAdjacencyMatches:before, privateAdjacencyControl:controlMatches,
                  firefoxViewHidden:root.hasAttribute('firefoxviewhidden'), privateRoot:root.hasAttribute('privatebrowsingmode'),
                  previousSibling:previous && {tag:previous.localName,id:previous.id},
                  tabsDisplay:getComputedStyle(document.getElementById('TabsToolbar')).display,
                  indicators:nodes.map(node => { const style=getComputedStyle(node), rect=node.getBoundingClientRect();
                    return {classes:node.className, parent:node.parentElement?.id,
                      display:style.display, visibility:style.visibility, width:rect.width, height:rect.height}; })};
                """, sandbox='system')
            if (not result['privateRoot'] or result['firefoxViewHidden']
                    or result['privateAdjacencyControl'] != 1):
                raise RuntimeError(f'private adjacency control failed: {result}')
            if len(result['indicators']) != 4:
                raise RuntimeError(f'private indicator fixture changed: {result}')
            result['visibleIndicators'] = sum(1 for node in result['indicators']
                                              if node['display'] != 'none' and node['visibility'] == 'visible'
                                              and node['width'] > 0 and node['height'] > 0)
            intervention = None
            if themed and not vertical and not result['tabsHidden']:
                intervention = m.execute_script("""
                    const toolbox = document.getElementById('navigator-toolbox');
                    toolbox.setAttribute('tabs-hidden', 'true');
                    const nodes = [...document.querySelectorAll('#nav-bar .private-browsing-indicator-with-label, #nav-bar .private-browsing-indicator-button')];
                    const visible = nodes.filter(node => { const s=getComputedStyle(node), r=node.getBoundingClientRect();
                      return s.display !== 'none' && s.visibility === 'visible' && r.width > 0 && r.height > 0; });
                    const count = visible.length;
                    toolbox.removeAttribute('tabs-hidden');
                    return {visibleNavbarIndicators:count};
                    """, sandbox='system')
            print(json.dumps({'firefox': metadata, 'themed': themed, 'result': result,
                              'tabsHiddenIntervention': intervention}), flush=True)
            if (not themed or vertical) and result['visibleIndicators'] == 0:
                raise RuntimeError(f'positive-control indicator is not visible: {result}')
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
        for themed, vertical in ((False, False), (True, False), (True, True)):
            check(nova, themed, vertical)
