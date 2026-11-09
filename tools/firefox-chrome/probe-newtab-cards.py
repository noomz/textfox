#!/usr/bin/env python3
"""Inspect recent-activity card selectors in disposable Firefox profiles.

The probe enables new-tab feeds, which may make network requests.
"""
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
PREFIX = 'browser.newtabpage.activity-stream.'


def check(browser_nova, newtab_nova, themed, page):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-newtab-cards-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-border-width: 7px; --tf-rounding: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 PREFIX + 'nova.enabled': newtab_nova,
                 PREFIX + 'testing.shouldInitializeFeeds': True,
                 PREFIX + 'widgets.enabled': True,
                 PREFIX + 'feeds.section.highlights': True,
                 PREFIX + 'section.highlights.includeBookmarks': True,
                 PREFIX + 'feeds.topsites': False,
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
                  browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),
                  highlights:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.feeds.section.highlights'),
                  includeBookmarks:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.section.highlights.includeBookmarks'),
                  topSites:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.feeds.topsites')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['browserNova'] != browser_nova or metadata['newtabNova'] != newtab_nova
                    or not metadata['highlights'] or not metadata['includeBookmarks'] or metadata['topSites']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            seeded = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                Promise.all(Array.from({length:5},(_,i)=>{
                  const url=`https://example.invalid/tf-card-${i}`;
                  return PlacesUtils.history.insert({url,visits:[{date:new Date(),transition:PlacesUtils.history.TRANSITIONS.LINK}]})
                    .then(()=>PlacesUtils.bookmarks.insert({parentGuid:PlacesUtils.bookmarks.unfiledGuid,url,title:`Textfox test ${i}`}));
                })).then(items=>done(items.length),error=>done(String(error)));
                """, sandbox='system')
            if seeded != 5:
                raise RuntimeError(f'visited bookmark fixture failed: {seeded}')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab(arguments[0])', script_args=[page], sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script("return location.href === arguments[0] && !!document.querySelector('section.compact-cards')", script_args=[page]):
                if time.monotonic() >= deadline:
                    raise RuntimeError('recent-activity section did not initialize')
                time.sleep(.2)
            m.set_context('chrome')
            m.execute_script("Services.prefs.setBoolPref(arguments[0], false)",
                             script_args=[PREFIX + 'section.highlights.includeBookmarks'], sandbox='system')
            time.sleep(1)
            m.execute_script("Services.prefs.setBoolPref(arguments[0], true)",
                             script_args=[PREFIX + 'section.highlights.includeBookmarks'], sandbox='system')
            m.set_context('content')
            deadline = time.monotonic() + 30
            while True:
                result = m.execute_script("""
                    const card=[...document.querySelectorAll('.card-outer')]
                      .find(e => e.textContent.includes('Textfox test'));
                    if (!card) return null;
                    const inspect=e=>{if(!e)return null; const s=getComputedStyle(e),r=e.getBoundingClientRect();
                      return {display:s.display,visibility:s.visibility,width:r.width,height:r.height,
                        border:s.borderTopWidth,borderStyle:s.borderTopStyle,radius:s.borderTopLeftRadius};};
                    return {url:location.href, text:card.textContent.trim(), outer:inspect(card),
                      card:inspect(card.querySelector('.card')),
                      preview:inspect(card.querySelector('.card-preview-image-outer')),
                      context:inspect(card.querySelector('.card-context')),
                      contextText:card.querySelector('.card-context')?.textContent.trim(),
                      contextMarkup:card.querySelector('.card-context')?.outerHTML,
                      cardAriaHidden:card.getAttribute('aria-hidden')};
                    """)
                if result:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('visited bookmark card did not initialize')
                time.sleep(.2)
            if (result['url'] != page or not result['card'] or not result['preview']
                    or not result['context'] or result['outer']['width'] <= 0 or result['outer']['height'] <= 0):
                raise RuntimeError(f'card fixture incomplete: {result}')
            if themed and (result['outer']['border'] != '7px' or result['outer']['borderStyle'] != 'solid'
                           or result['outer']['radius'] != '3px' or result['card']['radius'] != '3px'
                           or result['preview']['radius'] != '3px' or result['context']['display'] != 'none'):
                raise RuntimeError(f'configured card styles did not apply: {result}')
            card = m.execute_script("return [...document.querySelectorAll('.card-outer')].find(e => e.textContent.includes('Textfox test'))")
            link = card.find_element('css selector', 'a')
            link_properties = link.accessibility_properties
            pending = list(link_properties['children'])
            descendants = []
            while pending:
                if len(descendants) >= 100:
                    raise RuntimeError('card accessibility subtree exceeded probe limit')
                node = m.get_accessibility_properties_for_accessibility_node(pending.pop())
                descendants.append({'label':node.get('label'), 'role':node.get('role')})
                pending.extend(node.get('children', []))
            context_label = card.find_element('css selector', '.card-context-label')
            context_accessibility = context_label.accessibility_properties
            accessibility = {'linkLabel':link.computed_label, 'linkRole':link.computed_role,
                             'descendants':descendants, 'contextLabel':context_accessibility}
            if accessibility['linkRole'] != 'link' or 'Textfox test' not in accessibility['linkLabel']:
                raise RuntimeError(f'card link accessibility fixture unavailable: {accessibility}')
            print(json.dumps({'firefox':metadata, 'themed':themed, 'result':result,
                              'accessibility':accessibility}), flush=True)
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
    for browser_nova in (False, True):
        for newtab_nova in (False, True):
            for themed in (False, True):
                for page in ('about:newtab', 'about:home'):
                    check(browser_nova, newtab_nova, themed, page)
