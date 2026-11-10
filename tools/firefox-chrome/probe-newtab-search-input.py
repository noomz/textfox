#!/usr/bin/env python3
"""Inspect non-handoff search input in disposable Firefox profiles.

The disposable new-tab profile enables feeds and may make network requests.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.errors import NoSuchElementException, StaleElementException

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(browser_nova, newtab_nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-search-input-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-border-width: 7px; --tf-rounding: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'browser.newtabpage.activity-stream.improvesearch.handoffToAwesomebar': False,
                 'browser.search.suggest.enabled': False,
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
                return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version:Services.appinfo.version, build:Services.appinfo.appBuildID,
                  browserNova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled'),
                  handoff:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.improvesearch.handoffToAwesomebar')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['browserNova'] != browser_nova or metadata['newtabNova'] != newtab_nova
                    or metadata['handoff']):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            seeded = m.execute_async_script("""
                const done=arguments[arguments.length-1],{FormHistory}=ChromeUtils.importESModule('resource://gre/modules/FormHistory.sys.mjs');
                FormHistory.update({op:'bump',fieldname:'searchbar-history',value:'textfoxprobe saved fixture'})
                  .then(()=>done(true),error=>done(String(error)));
                """, sandbox='system')
            if seeded is not True:
                raise RuntimeError(f'local form-history fixture failed: {seeded}')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            selector = '.search-wrapper content-search-handoff-ui'
            deadline = time.monotonic() + 30
            while not m.execute_script("""
                const h=document.querySelector(arguments[0]);
                const b=h?.shadowRoot?.querySelector('#newtab-search-text');
                return location.href === 'about:newtab' && !!b && b.getBoundingClientRect().width > 0 && !h.hasAttribute('disabled') && !!document.querySelector('.top-site-outer .tile');
                """, script_args=[selector]):
                if time.monotonic() >= deadline:
                    raise RuntimeError('non-handoff search fixture did not initialize')
                time.sleep(.2)
            measurements = None
            deadline = time.monotonic() + 30
            while measurements is None:
                measurements = m.execute_script("""
                const wrapper=document.querySelector('.search-wrapper');
                const host=wrapper?.querySelector('content-search-handoff-ui');
                const input=host?.shadowRoot?.querySelector('#newtab-search-text');
                const tileElement=document.querySelector('.top-site-outer .tile');
                if (!input || !tileElement) return null;
                const root=getComputedStyle(document.documentElement);
                const tile=getComputedStyle(tileElement);
                function sample() {
                  const s=getComputedStyle(input), rect=input.getBoundingClientRect();
                  return {lightDomInput:!!wrapper.querySelector('input'), shadowInput:!!input,
                    nonhandoff:host.getAttribute('nonhandoff'),
                    width:rect.width, height:rect.height, border:s.borderTopWidth,
                    borderStyle:s.borderTopStyle, borderColor:s.borderTopColor, radius:s.borderTopLeftRadius,
                    background:s.backgroundColor, shadow:s.boxShadow,
                    tfWidth:root.getPropertyValue('--tf-border-width').trim(),
                    tfRounding:root.getPropertyValue('--tf-rounding').trim(),
                    tileBorder:tile.borderTopWidth, tileRadius:tile.borderTopLeftRadius};
                }
                const baseline=sample();
                host.style.setProperty('--content-search-handoff-ui-border-width', '7px');
                host.style.setProperty('--content-search-handoff-ui-border-radius', '3px');
                const control=sample();
                host.style.removeProperty('--content-search-handoff-ui-border-width');
                host.style.removeProperty('--content-search-handoff-ui-border-radius');
                return {baseline, control};
                """)
                if measurements is None:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('non-handoff search disappeared during measurement')
                    time.sleep(.1)
            baseline = measurements['baseline']
            control = measurements['control']
            if baseline['width'] <= 0 or baseline['height'] <= 0 or baseline['nonhandoff'] != 'true':
                raise RuntimeError('not a visible non-handoff fixture')
            if themed and (baseline['tfWidth'] != '7px' or baseline['tfRounding'] != '3px'
                           or baseline['tileBorder'] != '7px' or baseline['tileRadius'] != '3px'):
                raise RuntimeError('configured properties did not reach the shortcut control')
            if control['border'] != '7px' or control['radius'] != '3px':
                raise RuntimeError('host variable control did not affect shadow input')
            m.execute_script("document.body.tabIndex = -1; document.body.focus();")
            m.set_context('chrome')
            if m.execute_script('return gURLBar.focused', sandbox='system'):
                raise RuntimeError('URL bar was already focused before search input click')
            m.set_context('content')
            deadline = time.monotonic() + 10
            stale_retries = 0
            missing_retries = 0
            while True:
                try:
                    m.find_element('css selector', selector).click()
                    break
                except NoSuchElementException:
                    missing_retries += 1
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
                except StaleElementException:
                    stale_retries += 1
                    if time.monotonic() >= deadline:
                        raise
            deadline = time.monotonic() + 10
            while not m.execute_script("""
                const h=document.querySelector('.search-wrapper content-search-handoff-ui');
                return !!h && h.shadowRoot?.activeElement?.id === 'newtab-search-text';
                """):
                if time.monotonic() >= deadline:
                    raise RuntimeError('non-handoff click did not focus the search input')
                time.sleep(.1)
            m.actions.sequence('key', 'keyboard').send_keys('textfoxprobe').perform()
            value = m.execute_script("""
                const h=document.querySelector('.search-wrapper content-search-handoff-ui');
                return h?.shadowRoot?.querySelector('#newtab-search-text')?.value;
                """)
            if value != 'textfoxprobe':
                raise RuntimeError(f'non-handoff input did not receive typed text: {value!r}')
            deadline=time.monotonic()+15
            while True:
                suggestions=m.execute_script("""
                    const host=document.querySelector('.search-wrapper content-search-handoff-ui');
                    const table=host?.shadowRoot?.querySelector('.contentSearchSuggestionTable');
                    if(!table)return null;
                    const s=getComputedStyle(table),r=table.getBoundingClientRect();
                    return {display:s.display,visibility:s.visibility,width:r.width,height:r.height,
                      borderToken:s.getPropertyValue('--newtab-border-color').trim(),
                      text:table.textContent.includes('textfoxprobe saved fixture')};
                    """)
                if suggestions and suggestions['text'] and suggestions['width']>0 and suggestions['height']>0:
                    break
                if time.monotonic()>=deadline:
                    raise RuntimeError(f'local non-handoff suggestion did not appear: {suggestions}')
                time.sleep(.1)
            suggestion_control=m.execute_script("""
                const host=document.querySelector('.search-wrapper content-search-handoff-ui');
                const table=host.shadowRoot.querySelector('.contentSearchSuggestionTable');
                const oneoffs=table.querySelector('.contentSearchOneOffsTable'),body=document.body;
                if(!oneoffs)throw new Error('non-handoff one-offs table missing');
                const name='--newtab-border-color', saved=[body,table].map(e=>({e,value:e.style.getPropertyValue(name),priority:e.style.getPropertyPriority(name)}));
                const sample=()=>({token:getComputedStyle(table).getPropertyValue(name).trim(),border:getComputedStyle(oneoffs).borderBlockStartColor});
                const before=sample();
                try {
                  body.style.setProperty(name,'rgb(19, 83, 137)','important');
                  const inherited=sample();
                  table.style.setProperty(name,'rgb(173, 89, 41)','important');
                  return {before,inherited,direct:sample()};
                } finally {
                  for(const {e,value,priority} of saved) {if(value)e.style.setProperty(name,value,priority);else e.style.removeProperty(name);}
                }
                """)
            if (suggestion_control['before']!=suggestion_control['inherited']
                    or suggestion_control['direct']['border']!='rgb(173, 89, 41)'):
                raise RuntimeError(f'suggestion table border control failed: {suggestion_control}')
            m.set_context('chrome')
            if m.execute_script('return gURLBar.focused', sandbox='system'):
                raise RuntimeError('non-handoff click unexpectedly focused URL bar')
            print(json.dumps({'firefox': metadata, 'themed': themed, 'baseline': baseline,
                              'suggestions':suggestions,'suggestionControl':suggestion_control,
                              'control': control, 'inputValue': value,
                              'staleClickRetries': stale_retries,
                              'missingClickRetries': missing_retries}), flush=True)
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
                check(browser_nova, newtab_nova, themed)
