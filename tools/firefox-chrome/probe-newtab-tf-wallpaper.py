#!/usr/bin/env python3
"""Check --tf-newtab-wallpaper on about:newtab in disposable Firefox profiles.

A configured image fills in only while Firefox has no wallpaper of its own; a wallpaper picked
in Customize keeps priority. Requires the live-tool venv Python; set FIREFOX to pick a binary.
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
IMAGE = ROOT / 'prototypes/nova-thermal/themes/cinder-wallpaper.png'
FIREFOX = os.environ.get('FIREFOX', 'firefox')


def check(configured, firefox_wallpaper, preset=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-newtab-wallpaper-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        if configured:
            shutil.copy(IMAGE, profile / 'chrome/wallpaper.png')
            (profile / 'chrome/config.css').write_text(':root { --tf-newtab-wallpaper: url("wallpaper.png"); }\n' + (
                # A preset that paints body itself, like Thermal Atlas' animated export, wins.
                '@-moz-document url("about:newtab") { body { background-image: linear-gradient(red, red) !important; } }\n'
                if preset else ''))
        prefs = {'marionette.port': port, 'browser.nova.enabled': True,
                 PREFIX + 'nova.enabled': True,
                 PREFIX + 'testing.shouldInitializeFeeds': True,
                 PREFIX + 'newtabWallpapers.enabled': True,
                 PREFIX + 'newtabWallpapers.user.enabled': True,
                 PREFIX + 'newtabWallpapers.wallpaper': firefox_wallpaper,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen([FIREFOX, '--marionette', '-remote-allow-system-access', '--headless',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            m.execute_script('gBrowser.selectedTab=gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while True:
                state = m.execute_script("""
                    const body=document.body;
                    if (location.href !== 'about:newtab' || !body || !document.querySelector('#root > *')) return null;
                    const image=getComputedStyle(body).backgroundImage;
                    const loaded=new Promise(done => {
                      const url=image.match(/url\\("([^"]+wallpaper\\.png)"\\)/)?.[1];
                      if (!url) return done(null);
                      const probe=new Image(); probe.onload=()=>done(probe.naturalWidth); probe.onerror=()=>done(0);
                      probe.src=url;
                    });
                    return loaded.then(width => ({image, width,
                      inline:body.style.getPropertyValue('--newtab-wallpaper')}));
                    """)
                if state and (not firefox_wallpaper or state['inline']):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'new tab did not initialize: {state}')
                time.sleep(.2)
            ours = 'wallpaper.png' in state['image']
            expected = configured and not firefox_wallpaper and not preset
            if ours != expected or (ours and not state['width']) or (preset and 'rgb(255, 0, 0)' not in state['image']):
                raise RuntimeError(f'configured={configured} firefox={firefox_wallpaper!r}: {state}')
            print(json.dumps({'configured': configured, 'firefoxWallpaper': firefox_wallpaper, 'preset': preset, **state}), flush=True)
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
    for configured in (False, True):
        for firefox_wallpaper in ('', 'solid-color-picker-#14283c'):
            check(configured, firefox_wallpaper)
    check(True, '', preset=True)
    print('--tf-newtab-wallpaper: shown only while neither Firefox nor a preset paints the page; all cases passed')
