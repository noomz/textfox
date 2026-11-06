#!/usr/bin/env python3
"""Inspect in-page moz-urlbar search controls in disposable Firefox profiles.

The disposable new-tab profile enables feeds and may make network requests.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from marionette_driver.errors import NoSuchElementException, StaleElementException
from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(browser_nova, newtab_nova, themed, variant, contrast_cycle=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-newtab-urlbar-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-border-width: 7px; --tf-rounding: 3px; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': browser_nova,
                 'browser.newtabpage.activity-stream.nova.enabled': newtab_nova,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'browser.newtabpage.activity-stream.improvesearch.handoffToAwesomebar': True,
                 'browser.urlbar.newtab.featureGate': True,
                 'browser.urlbar.newtab.variantA': variant in ('a', 'both'),
                 'browser.urlbar.newtab.variantB': variant in ('b', 'both'),
                 'browser.search.suggest.enabled': False,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        if contrast_cycle:
            prefs['ui.useAccessibilityTheme'] = 0
            prefs['browser.display.document_color_use'] = 0
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
                  handoff:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.improvesearch.handoffToAwesomebar'),
                  gatePref:Services.prefs.getBoolPref('browser.urlbar.newtab.featureGate'),
                  variantAPref:Services.prefs.getBoolPref('browser.urlbar.newtab.variantA'),
                  variantBPref:Services.prefs.getBoolPref('browser.urlbar.newtab.variantB'),
                  gate:ChromeUtils.importESModule('moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs').UrlbarPrefs.get('newtabFeatureGate'),
                  variantA:ChromeUtils.importESModule('moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs').UrlbarPrefs.get('newtabVariantA'),
                  variantB:ChromeUtils.importESModule('moz-src:///browser/components/urlbar/UrlbarPrefs.sys.mjs').UrlbarPrefs.get('newtabVariantB')};
                """, sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['browserNova'] != browser_nova or metadata['newtabNova'] != newtab_nova
                    or not metadata['handoff'] or not metadata['gatePref']
                    or metadata['variantAPref'] != (variant in ('a', 'both'))
                    or metadata['variantBPref'] != (variant in ('b', 'both'))
                    or metadata['gate'] != browser_nova
                    or metadata['variantA'] != (browser_nova and variant in ('a', 'both'))
                    or metadata['variantB'] != (browser_nova and variant in ('b', 'both'))):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while True:
                result = m.execute_script("""
                    const wrapper=document.querySelector('.search-wrapper');
                    const host=wrapper?.querySelector('moz-urlbar');
                    const handoff=wrapper?.querySelector('content-search-handoff-ui');
                    if (!wrapper || !document.querySelector('.top-site-outer .tile')) return null;
                    if (arguments[0] && !host?.querySelector('input.urlbar-input')) return null;
                    if (!arguments[0] && !handoff?.shadowRoot?.querySelector('.search-handoff-button')) return null;
                    const inspect=e=>{
                      if (!e) return null;
                      const s=getComputedStyle(e), r=e.getBoundingClientRect();
                      return {width:r.width,height:r.height,x:r.x,y:r.y,border:s.borderTopWidth,
                        borderColor:s.borderTopColor,borderStyle:s.borderTopStyle,
                        radius:s.borderTopLeftRadius,background:s.backgroundColor,outline:s.outlineStyle};
                    };
                    const root=getComputedStyle(document.documentElement);
                    const switcher=host?.querySelector('.searchmode-switcher');
                    if (host && (!switcher || !switcher.getBoundingClientRect().width)) return null;
                    return {moz:!!host,handoff:!!handoff,shadow:!!host?.shadowRoot,
                      variantA:host?.hasAttribute('variant-a'),variantB:host?.hasAttribute('variant-b'),
                      switcher:inspect(switcher), switcherType:switcher?.getAttribute('type'),
                      wordmark:switcher?.getAttribute('wordmark'),
                      input:inspect(host?.querySelector('input.urlbar-input')),
                      background:inspect(host?.querySelector('.urlbar-background')),
                      tile:inspect(document.querySelector('.top-site-outer .tile')),
                      tfWidth:root.getPropertyValue('--tf-border-width').trim(),
                      tfRounding:root.getPropertyValue('--tf-rounding').trim()};
                    """, script_args=[browser_nova])
                if result:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('in-page search fixture did not initialize')
                time.sleep(.2)
            if browser_nova:
                if (result['handoff'] or result['shadow'] or result['input']['width'] <= 0
                        or result['variantA'] != (variant == 'a')
                        or result['variantB'] != (variant in ('b', 'both'))):
                    raise RuntimeError(f'unexpected moz-urlbar structure: {result}')
                if variant in ('b', 'both') and result['switcher']['y'] + result['switcher']['height'] > result['input']['y'] + 1:
                    raise RuntimeError(f'variant B switcher is not above the input: {result}')
                if themed and (result['tfWidth'] != '7px' or result['tfRounding'] != '3px'
                               or result['input']['border'] != '7px' or result['input']['radius'] != '3px'
                               or result['tile']['border'] != '7px' or result['tile']['radius'] != '3px'):
                    raise RuntimeError(f'configured theme styles did not reach controls: {result}')
                switcher_replacements = 0
                if variant != 'default':
                    switcher_selector = '.search-wrapper moz-urlbar .searchmode-switcher'
                    deadline = time.monotonic() + 15
                    while True:
                        marked = m.execute_script("""
                            const b=document.querySelector(arguments[0]);
                            if (!b) return false;
                            b.setAttribute('data-tf-probe-id', arguments[1]);
                            return true;
                            """, script_args=[switcher_selector, str(switcher_replacements)])
                        try:
                            if not marked:
                                raise NoSuchElementException('in-page switcher not present')
                            m.find_element('css selector', switcher_selector).click()
                        except (NoSuchElementException, StaleElementException):
                            switcher_replacements += 1
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(.1)
                            continue
                        while True:
                            menu = m.execute_script("""
                                const h=document.querySelector('.search-wrapper moz-urlbar');
                                const b=h?.querySelector('.searchmode-switcher');
                                return {same:b?.getAttribute('data-tf-probe-id') === arguments[0],
                                  open:!!h?.querySelector('.searchmode-switcher-panel-list:popover-open')};
                                """, script_args=[str(switcher_replacements)])
                            if not menu['same'] or menu['open']:
                                break
                            if time.monotonic() >= deadline:
                                raise RuntimeError('search engine switcher menu did not open')
                            time.sleep(.1)
                        if menu['same'] and menu['open']:
                            m.actions.sequence('key', 'keyboard').send_keys(Keys.ESCAPE).perform()
                            while True:
                                menu = m.execute_script("""
                                    const h=document.querySelector('.search-wrapper moz-urlbar');
                                    const b=h?.querySelector('.searchmode-switcher');
                                    return {same:b?.getAttribute('data-tf-probe-id') === arguments[0],
                                      open:!!h?.querySelector('.searchmode-switcher-panel-list:popover-open')};
                                    """, script_args=[str(switcher_replacements)])
                                if not menu['same'] or not menu['open']:
                                    break
                                if time.monotonic() >= deadline:
                                    raise RuntimeError('Escape did not close the search engine switcher menu')
                                time.sleep(.1)
                            if menu['same']:
                                break
                        switcher_replacements += 1
                        if time.monotonic() >= deadline:
                            raise RuntimeError('in-page switcher was repeatedly replaced during interaction')
                selector = '.search-wrapper moz-urlbar input.urlbar-input'
                deadline = time.monotonic() + 15
                replaced = 0
                while True:
                    marked = m.execute_script("""
                        const i=document.querySelector(arguments[0]);
                        if (!i) return false;
                        i.setAttribute('data-tf-probe-id', arguments[1]);
                        return true;
                        """, script_args=[selector, str(replaced)])
                    try:
                        if not marked:
                            raise NoSuchElementException('in-page input not present')
                        m.find_element('css selector', selector).click()
                    except (NoSuchElementException, StaleElementException):
                        replaced += 1
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.1)
                        continue
                    while True:
                        focus = m.execute_script("""
                            const i=document.querySelector(arguments[0]);
                            return {same:i?.getAttribute('data-tf-probe-id') === arguments[1],
                                    focused:!!i && document.activeElement === i};
                            """, script_args=[selector, str(replaced)])
                        if not focus['same'] or focus['focused']:
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError('in-page input did not gain focus after click')
                        time.sleep(.1)
                    if focus['focused'] and focus['same']:
                        m.actions.sequence('key', 'keyboard').send_keys('textfoxprobe').perform()
                        while True:
                            state = m.execute_script("""
                                const h=document.querySelector('.search-wrapper moz-urlbar');
                                const i=h?.querySelector('input.urlbar-input');
                                return {same:i?.getAttribute('data-tf-probe-id') === arguments[0],
                                  value:i?.value, focused:!!i && document.activeElement === i,
                                  popover:h?.querySelector('.urlbarView')?.matches(':popover-open')};
                                """, script_args=[str(replaced)])
                            if not state['same'] or (state['value'] == 'textfoxprobe' and state['focused']):
                                break
                            if time.monotonic() >= deadline:
                                raise RuntimeError(f'in-page search input did not receive text: {state}')
                            time.sleep(.1)
                        if state['same']:
                            break
                    replaced += 1
                    if time.monotonic() >= deadline:
                        raise RuntimeError('in-page input was repeatedly replaced during interaction')
            else:
                if result['moz'] or not result['handoff']:
                    raise RuntimeError(f'expected handoff fallback: {result}')
                state=None
            contrast = None
            if browser_nova and contrast_cycle:
                selector = '.search-wrapper moz-urlbar'
                m.execute_script("document.querySelector(arguments[0]).setAttribute('data-tf-contrast-cycle', 'same')", script_args=[selector])
                def read_switcher():
                    return m.execute_script("""
                        const h=document.querySelector('.search-wrapper moz-urlbar');
                        const b=h?.querySelector('.searchmode-switcher');
                        const title=b?.querySelector('.searchmode-switcher-title');
                        const word=b?.querySelector('.searchmode-switcher-wordmark');
                        const tr=title?.getBoundingClientRect(), wr=word?.getBoundingClientRect();
                        return {same:h?.getAttribute('data-tf-contrast-cycle') === 'same',
                          contrast:matchMedia('(prefers-contrast)').matches,
                          forced:matchMedia('(forced-colors: active)').matches,
                          wordmark:b?.getAttribute('wordmark'), icon:!!b?.getAttribute('iconsrc'),
                          label:b?.getAttribute('aria-label'), title:title?.textContent.trim(),
                          titleDisplay:title && getComputedStyle(title).display,
                          titleWidth:tr?.width, titleHeight:tr?.height,
                          wordmarkImage:word && getComputedStyle(word).backgroundImage,
                          wordmarkWidth:wr?.width, wordmarkHeight:wr?.height};
                        """)
                def wait_switcher(high):
                    deadline = time.monotonic() + 15
                    while True:
                        sample = read_switcher()
                        if not sample['same']:
                            raise RuntimeError(f'search host changed during contrast cycle: {sample}')
                        media_matches = sample['contrast'] == high and sample['forced'] == high
                        if high:
                            ready = (sample['wordmark'] is None and sample['icon'] and sample['title'] == 'Google'
                                     and sample['titleDisplay'] != 'none' and sample['titleWidth'] > 0
                                     and sample['titleHeight'] > 0)
                        else:
                            ready = (sample['wordmark'] == 'google' and sample['wordmarkWidth'] > 0
                                     and sample['wordmarkHeight'] > 0 and '/engine-wordmarks/' in sample['wordmarkImage'])
                        ready = media_matches and ready
                        if ready:
                            return sample
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'search switcher did not reach expected contrast state: {sample}')
                        time.sleep(.1)
                normal = wait_switcher(False)
                m.set_context('chrome')
                m.execute_script("Services.prefs.setIntPref('ui.useAccessibilityTheme', 1); Services.prefs.setIntPref('browser.display.document_color_use', 2);", sandbox='system')
                m.set_context('content')
                high = wait_switcher(True)
                m.set_context('chrome')
                m.execute_script("Services.prefs.setIntPref('ui.useAccessibilityTheme', 0); Services.prefs.setIntPref('browser.display.document_color_use', 0);", sandbox='system')
                m.set_context('content')
                restored = wait_switcher(False)
                contrast = {'normal': normal, 'high': high, 'restored': restored}
            print(json.dumps({'firefox':metadata,'themed':themed,'variant':variant,'result':result,'inputState':state,
                              'contrastCycle':contrast,
                              'interactionReplacements':replaced if browser_nova else None,
                              'switcherReplacements':switcher_replacements if browser_nova and variant != 'default' else None}),flush=True)
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
    parser.add_argument('--variant', choices=('default', 'a', 'b', 'both'), default='default')
    parser.add_argument('--contrast-cycle', action='store_true')
    args = parser.parse_args()
    if args.contrast_cycle and args.variant == 'default':
        parser.error('--contrast-cycle requires a, b, or both')
    for browser_nova in (False, True):
        for newtab_nova in (False, True):
            for themed in (False, True):
                check(browser_nova, newtab_nova, themed, args.variant, args.contrast_cycle)
