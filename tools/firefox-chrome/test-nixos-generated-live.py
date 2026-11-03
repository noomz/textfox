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
    args = parser.parse_args()
    if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', args.nixpkgs_path):
        parser.error('nixpkgs_path must be a literal /nix/store path')
    env = os.environ.copy()
    env['NP_RUNTIME'] = 'bwrap'
    expression = f'import ./tools/firefox-chrome/check-nixos-build.nix {{ nixpkgsPath = {args.nixpkgs_path}; }}'
    output = subprocess.check_output([str(Path.home() / '.local/bin/nix-portable'), 'nix', 'build',
        '--offline', '--impure', '--no-link', '--expr', expression, '--print-out-paths'],
        cwd=ROOT, env=env, text=True, timeout=180).strip()
    config = subprocess.check_output(['nix', 'store', 'cat', output], text=True, timeout=30)
    match = re.search(r'var textfoxChrome = new FileUtils.File\("(/nix/store/[^"\n]+)"\)', config)
    if not match:
        raise RuntimeError('generated autoconfig does not identify CSS store directory')
    store_dir = match.group(1)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-nixos-generated-') as directory:
        profile = Path(directory)
        chrome = profile / 'chrome'
        chrome.mkdir()
        for name in ('userChrome.css', 'userContent.css'):
            (chrome / name).write_bytes(subprocess.check_output(['nix', 'store', 'cat', f'{store_dir}/{name}'], timeout=30))
        generated_chrome = (chrome / 'userChrome.css').read_text()
        if f'{store_dir}/icons/book.svg' not in generated_chrome:
            raise RuntimeError('generated CSS does not reference store icons')
        icon = subprocess.check_output(['nix', 'store', 'cat', f'{store_dir}/icons/book.svg'], timeout=30)
        if b'<svg' not in icon:
            raise RuntimeError('generated icon store path is invalid')
        (chrome / 'config.css').write_text(':root { --tf-import-config-sentinel: imported; }\n')
        (chrome / 'browser.css').write_text(':root { --tf-import-browser-sentinel: imported; }\n')
        (chrome / 'userChrome.css').write_text('@import url("control.css");\n' + (chrome / 'userChrome.css').read_text())
        (chrome / 'control.css').write_text(':root { --tf-import-control-sentinel: imported; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': False,
                 'browser.newtabpage.activity-stream.nova.enabled': True,
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
            meta = m.execute_script("return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled')}", sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova']:
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
                        controlSentinel:root.getPropertyValue('--tf-import-control-sentinel').trim(),
                        configSentinel:root.getPropertyValue('--tf-import-config-sentinel').trim(),
                        browserSentinel:root.getPropertyValue('--tf-import-browser-sentinel').trim()};
                """, sandbox='system')
            print(json.dumps({'firefox':meta, 'autoconfig':output, 'storeCss':store_dir, 'result':result}), flush=True)
            if result['configuredBorder'] != '7px' or result['borderWidth'] != '7px':
                raise RuntimeError('generated CSS did not apply configured border')
            if result['controlSentinel'] != 'imported':
                raise RuntimeError('valid import control did not load')
            if result['configSentinel'] or result['browserSentinel']:
                raise RuntimeError('late generated imports unexpectedly loaded profile files')
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script('return location.href === "about:newtab" && !!document.querySelector(".nova-enabled .tile")'):
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
                const tile = getComputedStyle(document.querySelector('.nova-enabled .tile'));
                return {borderWidth:tile.borderTopWidth, borderStyle:tile.borderTopStyle,
                        color:tile.borderTopColor, borderToken:tile.getPropertyValue('--border-width').trim(),
                        tfBorder:tile.getPropertyValue('--tf-border-width').trim(),
                        borderColorToken:tile.getPropertyValue('--newtab-border-color').trim(),
                        radius:tile.borderRadius, shadow:tile.boxShadow,
                        forcedColors:matchMedia('(forced-colors: active)').matches,
                        wallpaper:document.body.style.getPropertyValue('--newtab-wallpaper-color').trim()};
                """)
            print(json.dumps({'generatedContent':content}), flush=True)
            if args.wallpaper and content['wallpaper'] != '#14283c':
                raise RuntimeError('requested Firefox wallpaper color not effective')
            if content['forcedColors'] != args.forced_colors or content['borderWidth'] != '7px' or content['borderStyle'] != 'solid' or content['radius'] != '0px' or content['shadow'] != 'none':
                raise RuntimeError('generated userContent CSS did not style Nova shortcut tile')
            m.execute_script("document.body.style.setProperty('--newtab-wallpaper-color', 'rgb(20, 40, 60)')")
            wallpaper = m.execute_script("""
                const s=getComputedStyle(document.querySelector('.nova-enabled .tile'));
                const probe=document.createElement('span');
                probe.style.color='Canvas'; document.body.appendChild(probe);
                const canvas=getComputedStyle(probe).color; probe.remove();
                return {width:s.borderTopWidth, style:s.borderTopStyle,
                        color:s.borderTopColor, token:s.getPropertyValue('--newtab-border-color').trim(), canvas,
                        background:getComputedStyle(document.body).backgroundColor};
                """)
            print(json.dumps({'generatedContentWallpaper':wallpaper}), flush=True)
            if wallpaper['width'] != '7px' or wallpaper['style'] != 'solid' or not wallpaper['token']:
                raise RuntimeError('generated userContent border failed with wallpaper color')
            if args.forced_colors:
                m.execute_script("""
                    document.body.style.setProperty('--newtab-wallpaper-color', 'Canvas');
                    document.body.style.setProperty('--newtab-contextual-text-primary-color', 'Canvas');
                    """)
                stress = m.execute_script("""
                    const tile=document.querySelector('.nova-enabled .tile');
                    const s=getComputedStyle(tile);
                    const p=document.createElement('span'); p.style.color='Canvas'; document.body.appendChild(p);
                    const canvas=getComputedStyle(p).color; p.remove();
                    const normalize=color => {
                      const c=document.createElement('canvas').getContext('2d');
                      c.fillStyle=color; c.fillRect(0, 0, 1, 1); return [...c.getImageData(0, 0, 1, 1).data];
                    };
                    return {border:s.borderTopColor, canvas, borderPixel:normalize(s.borderTopColor), canvasPixel:normalize(canvas)};
                    """)
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
