#!/usr/bin/env python3
"""Inspect actual Nix-generated CSS in disposable Firefox profiles.

The generated autoconfig is not executed. Requires a nix-portable bwrap runtime
and cached nixpkgs source. This is not a NixOS activation test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nixpkgs_path')
    parser.add_argument('--forced-colors', action='store_true')
    parser.add_argument('--wallpaper', action='store_true')
    parser.add_argument('--newtab-classic', action='store_true')
    parser.add_argument('--browser-nova', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', args.nixpkgs_path):
        parser.error('nixpkgs_path must be a literal /nix/store path')
    env = os.environ.copy()
    env['NP_RUNTIME'] = 'bwrap'
    expression = f'import ./tools/firefox-chrome/check-nixos-build.nix {{ nixpkgsPath = {args.nixpkgs_path}; }}'
    output = subprocess.check_output([str(Path.home() / '.local/bin/nix-portable'), 'nix', 'build',
        '--offline', '--impure', '--no-link', '--expr', expression, '--print-out-paths'],
        cwd=ROOT, env=env, text=True, timeout=300).strip()
    nix_portable = str(Path.home() / '.local/bin/nix-portable')

    def store_cat(path):
        return subprocess.check_output(['nix', 'store', 'cat', path], timeout=30)

    config = store_cat(output).decode()
    match = re.search(r'var storeDir = new FileUtils.File\("(/nix/store/[^"\n]+)"\)', config)
    if not match:
        raise RuntimeError('generated autoconfig does not identify CSS store directory')
    store_dir = match.group(1)
    subprocess.check_call([nix_portable, 'nix-store', '--realise', store_dir],
                          cwd=ROOT, env=env, stdout=subprocess.DEVNULL, timeout=300)
    manifest = store_cat(f'{store_dir}/manifest.sha256').decode()
    assets = re.search(r'^assetsDir (\S+)$', manifest, re.MULTILINE)
    if not assets:
        raise RuntimeError('generated manifest does not name its asset directory')
    asset_name = assets.group(1)
    asset_dir = f'{store_dir}/{asset_name}'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-nixos-generated-') as directory:
        profile = Path(directory)
        chrome = profile / 'chrome'
        chrome.mkdir()
        for name in ('userChrome.css', 'userContent.css'):
            (chrome / name).write_bytes(store_cat(f'{store_dir}/{name}'))
        generated_chrome = (chrome / 'userChrome.css').read_text()
        imports = re.findall(r'^@import\s+url\(["\']?([^"\')]+)', (ROOT / 'chrome' / 'userChrome.css').read_text(), re.MULTILINE)
        if not imports:
            raise RuntimeError('manual userChrome imports not found')
        for name in imports:
            if name == 'config.css':
                continue
            if name == 'defaults.css':
                marker = '--tf-font-family:'
            else:
                lines = [(line.strip()) for line in (ROOT / 'chrome' / name).read_text().splitlines()
                         if line.strip() and not line.lstrip().startswith(('/*', '*', '//'))]
                marker = lines[0]
            if marker not in generated_chrome:
                raise RuntimeError(f'generated CSS missing imported file marker: {name}: {marker}')
        if './icons/' in generated_chrome:
            raise RuntimeError('generated CSS retains a relative icon reference')
        if 'chrome://global/skin/icons/close.svg' not in generated_chrome:
            raise RuntimeError('generated CSS corrupted Firefox chrome icon URL')
        source_icons = (ROOT / 'chrome' / 'icons.css').read_text()
        firefox_urls = set(re.findall(r'chrome://[^\s\"\')]+', source_icons))
        if not firefox_urls or any(url not in generated_chrome for url in firefox_urls):
            raise RuntimeError('generated CSS changed Firefox chrome URLs')
        if 'nix/store' in generated_chrome:
            raise RuntimeError('generated CSS still points at store paths')
        asset_refs = set(re.findall(re.escape(asset_name) + r'/([\w.-]+)', generated_chrome))
        icon_names = {name for name in asset_refs if name.endswith('.svg')}
        if not icon_names:
            raise RuntimeError('generated CSS does not reference store icons')
        for name in sorted(icon_names):
            icon = store_cat(f'{asset_dir}/{name}')
            if b'<svg' not in icon:
                raise RuntimeError(f'generated icon store path is invalid: {name}')
        print(json.dumps({'generatedIconFiles':len(icon_names)}), flush=True)
        (chrome / 'config.css').write_text(':root { --tf-import-config-sentinel: imported; }\n')
        (chrome / 'browser.css').write_text(':root { --tf-import-browser-sentinel: imported; }\n')
        (chrome / 'userChrome.css').write_text('@import url("control.css");\n' + (chrome / 'userChrome.css').read_text())
        (chrome / 'control.css').write_text(':root { --tf-import-control-sentinel: imported; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': args.browser_nova,
                 'sidebar.revamp': True, 'sidebar.verticalTabs': True,
                 'browser.newtabpage.activity-stream.nova.enabled': not args.newtab_classic,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.newtabpage.activity-stream.widgets.enabled': True,
                 'browser.display.document_color_use': 2 if args.forced_colors else 1,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        if args.wallpaper:
            prefs.update({'browser.newtabpage.activity-stream.newtabWallpapers.enabled': True,
                          'browser.newtabpage.activity-stream.newtabWallpapers.user.enabled': True,
                          'browser.newtabpage.activity-stream.newtabWallpapers.wallpaper': 'solid-color-picker-#14283c'})
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),newtabNova:Services.prefs.getBoolPref('browser.newtabpage.activity-stream.nova.enabled')}", sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != args.browser_nova or meta['newtabNova'] != (not args.newtab_classic)):
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            deadline = time.monotonic() + 20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            result = m.execute_script("""
                const root = getComputedStyle(document.documentElement);
                const el = document.querySelector('#nav-bar');
                const s = getComputedStyle(el);
                return {configuredBorder:root.getPropertyValue('--tf-border-width').trim(),
                        borderWidth:s.borderTopWidth, borderStyle:s.borderTopStyle,
                        background:s.backgroundColor,
                        extraSentinel:s.getPropertyValue('--tf-extra-chrome-sentinel').trim(),
                        controlSentinel:root.getPropertyValue('--tf-import-control-sentinel').trim(),
                        configSentinel:root.getPropertyValue('--tf-import-config-sentinel').trim(),
                        browserSentinel:root.getPropertyValue('--tf-import-browser-sentinel').trim(),
                        verticalOption:root.getPropertyValue('--tf-display-vertical-tabs').trim(),
                        verticalStrip:(() => {
                          const strip = document.querySelector('box#vertical-tabs');
                          if (!strip) return null;
                          const style = getComputedStyle(strip), rect = strip.getBoundingClientRect();
                          return {display:style.display, width:rect.width, height:rect.height};
                        })()};
                """, sandbox='system')
            print(json.dumps({'firefox':meta, 'autoconfig':output, 'storeCss':store_dir, 'result':result}), flush=True)
            if result['extraSentinel'] != 'applied':
                raise RuntimeError('extraUserChrome rule did not apply')
            if result['configuredBorder'] != '11px' or result['borderWidth'] != '11px':
                raise RuntimeError('generated CSS did not apply configured border')
            if result['controlSentinel'] != 'imported':
                raise RuntimeError('valid import control did not load')
            if result['configSentinel'] or result['browserSentinel']:
                raise RuntimeError('late generated imports unexpectedly loaded profile files')
            if result['verticalOption'] != 'none' or not result['verticalStrip']:
                raise RuntimeError(f'generated CSS lost the vertical-tabs option: {result}')
            if result['verticalStrip']['display'] != 'none' or result['verticalStrip']['width'] != 0:
                raise RuntimeError(f'generated CSS did not hide the vertical strip: {result["verticalStrip"]}')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            selector = '.classic-enabled .tile' if args.newtab_classic else '.nova-enabled .tile'
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(arguments[0])', script_args=[selector]):
                if time.monotonic() >= deadline:
                    raise RuntimeError('generated userContent fixture did not initialize')
                time.sleep(.25)
            if args.wallpaper:
                deadline = time.monotonic() + 20
                while not m.execute_script("return getComputedStyle(document.body).getPropertyValue('--newtab-wallpaper-color').trim() === '#14283c'"):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('wallpaper fixture did not initialize')
                    time.sleep(.1)
            content = m.execute_script("""
                const tile = getComputedStyle(document.querySelector(arguments[0]));
                return {borderWidth:tile.borderTopWidth, borderStyle:tile.borderTopStyle,
                        color:tile.borderTopColor, borderToken:tile.getPropertyValue('--border-width').trim(),
                        tfBorder:tile.getPropertyValue('--tf-border-width').trim(),
                        borderColorToken:tile.getPropertyValue('--newtab-border-color').trim(),
                        radius:tile.borderRadius, shadow:tile.boxShadow,
                        forcedColors:matchMedia('(forced-colors: active)').matches,
                        wallpaper:document.body.style.getPropertyValue('--newtab-wallpaper-color').trim()};
                """, script_args=[selector])
            print(json.dumps({'generatedContent':content}), flush=True)
            if args.wallpaper and content['wallpaper'] != '#14283c':
                raise RuntimeError('requested Firefox wallpaper color not effective')
            if content['forcedColors'] != args.forced_colors or content['borderWidth'] != '13px' or content['borderStyle'] != 'solid' or content['radius'] != '0px' or content['shadow'] != 'none':
                raise RuntimeError('generated userContent CSS did not style Nova shortcut tile')
            m.execute_script("document.body.style.setProperty('--newtab-wallpaper-color', 'rgb(20, 40, 60)')")
            wallpaper = m.execute_script("""
                const s=getComputedStyle(document.querySelector(arguments[0]));
                const probe=document.createElement('span');
                probe.style.color='Canvas'; document.body.appendChild(probe);
                const canvas=getComputedStyle(probe).color; probe.remove();
                return {width:s.borderTopWidth, style:s.borderTopStyle,
                        color:s.borderTopColor, token:s.getPropertyValue('--newtab-border-color').trim(), canvas,
                        background:getComputedStyle(document.body).backgroundColor};
                """, script_args=[selector])
            print(json.dumps({'generatedContentWallpaper':wallpaper}), flush=True)
            if wallpaper['width'] != '13px' or wallpaper['style'] != 'solid' or not wallpaper['token']:
                raise RuntimeError('generated userContent border failed with wallpaper color')
            if args.forced_colors:
                m.execute_script("""
                    document.body.style.setProperty('--newtab-wallpaper-color', 'Canvas');
                    document.body.style.setProperty('--newtab-contextual-text-primary-color', 'Canvas');
                    """)
                stress = m.execute_script("""
                    const tile=document.querySelector(arguments[0]);
                    const s=getComputedStyle(tile);
                    const p=document.createElement('span'); p.style.color='Canvas'; document.body.appendChild(p);
                    const canvas=getComputedStyle(p).color; p.remove();
                    const normalize=color => {
                      const c=document.createElement('canvas').getContext('2d');
                      c.fillStyle=color; c.fillRect(0, 0, 1, 1); return [...c.getImageData(0, 0, 1, 1).data];
                    };
                    return {border:s.borderTopColor, canvas, borderPixel:normalize(s.borderTopColor), canvasPixel:normalize(canvas)};
                    """, script_args=[selector])
                print(json.dumps({'forcedColorStress':stress}), flush=True)
                if stress['borderPixel'] == stress['canvasPixel']:
                    raise RuntimeError('forced-colors tile border matches Canvas in stress fixture')
                negative = m.execute_script("""
                    const c=document.createElement('canvas').getContext('2d');
                    c.fillStyle=arguments[0]; c.fillRect(0,0,1,1);
                    return [...c.getImageData(0,0,1,1).data];
                    """, script_args=[stress['canvas']])
                if negative != stress['canvasPixel']:
                    raise RuntimeError('forced-colors comparison did not detect Canvas negative control')
            m.set_context('chrome')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:preferences")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 20
            while not m.execute_script('return location.href.startsWith("about:preferences") && !!document.querySelector("body")'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('preferences page did not load')
                time.sleep(.1)
            font = m.execute_script('return getComputedStyle(document.body).fontFamily')
            print(json.dumps({'generatedAboutPreferencesFont':font}), flush=True)
            if font != 'monospace':
                raise RuntimeError('generated userContent did not apply configured about-page font')
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
    main()
