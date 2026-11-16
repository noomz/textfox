#!/usr/bin/env python3
"""Report whether macOS Firefox draws the page context menu natively.

macOS Firefox can hand context menus to the OS (`widget.macos.native-context-menus`).
A native menu ignores CSS, so textfox's context-menu icons only paint when the
menu is a XUL popup. Starts a disposable textfox profile with the icon
preference on, opens the page context menu, and prints the preference value,
the popup state, and the Back item's geometry and computed icon. Set FIREFOX
to the binary; unset MOZ_HEADLESS for a headed run.

The last case writes the shipped `user.js` (textfox's managed preferences) and
sets nothing else about native menus. It fails unless that profile gets a XUL
popup with a visible Back item carrying textfox's icon (§B B9, T13). Only a
headed macOS run proves this; elsewhere the popup is always XUL.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def probe(native=None, shipped=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-macos-menu-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'shyfox.enable.context.menu.icons': True,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        if native is not None:
            prefs['widget.macos.native-context-menus'] = native
        shipped_prefs = (ROOT / 'user.js').read_text() if shipped else ''
        (profile / 'user.js').write_text(shipped_prefs + ''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = {**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'}
        process = subprocess.Popen([os.environ.get('FIREFOX', 'firefox'), '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('content')
            m.navigate('data:text/html,<p id="page" style="height:200px">textfox page fixture</p>')
            target = m.find_element('css selector', '#page')
            m.actions.sequence('pointer', 'mouse', {'pointerType': 'mouse'}).click(target, button=2).perform()
            m.set_context('chrome')
            deadline = time.monotonic() + 10
            while True:
                state = m.execute_script("""
                    const popup=document.getElementById('contentAreaContextMenu'), back=document.getElementById('context-back');
                    const r=back.getBoundingClientRect();
                    return {headless:Services.env.get('MOZ_HEADLESS')==='1', version:Services.appinfo.version,
                      build:Services.appinfo.appBuildID, native:Services.prefs.getBoolPref('widget.macos.native-context-menus'),
                      popupState:popup.state, backParent:back.parentElement.id, backWidth:r.width, backHeight:r.height,
                      backImage:getComputedStyle(back).backgroundImage};
                    """, sandbox='system')
                if state['popupState'] == 'open' or time.monotonic() >= deadline:
                    break
                time.sleep(.1)
            m.execute_script("document.getElementById('contentAreaContextMenu').hidePopup()", sandbox='system')
            return state
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


if __name__ == '__main__':
    for native in (None, False):
        print(json.dumps({'prefSet': native, **probe(native)}), flush=True)
    state = probe(shipped=True)
    print(json.dumps({'prefSet': 'shipped user.js', **state}), flush=True)
    xul = (state['native'] is False and state['popupState'] == 'open'
           and state['backWidth'] > 0 and state['backHeight'] > 0
           and state['backImage'] not in ('', 'none'))
    print('ok   shipped prefs keep a styled XUL context menu' if xul
          else 'not ok shipped prefs leave the context menu native or unstyled', flush=True)
    sys.exit(0 if xul else 1)
