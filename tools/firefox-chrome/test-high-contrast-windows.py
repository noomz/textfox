#!/usr/bin/env python3
"""Check chrome and new-tab text contrast with real Windows high contrast.

Runs on Windows, inside the interactive desktop session (Firefox needs a real
desktop; a service or SSH session has none). It switches the OS contrast theme
itself with SystemParametersInfo(SPI_SETHIGHCONTRAST), the call the Settings
app and the Left Alt+Left Shift+Print Screen shortcut use, and switches it back
off when it finishes.

Unlike test-high-contrast-live.py (Linux), no preference stands in for the OS:
ui.useAccessibilityTheme and browser.display.document_color_use stay unset.
Windows is the only platform whose default document_color_use is 0, so
high contrast alone must force new-tab content colors.

Cases: high contrast off and on; browser Nova off and on; vanilla and textfox
profiles. Every measured text must reach 4.5:1 against its nearest opaque
background. Set FIREFOX to the firefox.exe to test.
"""
import ctypes
from ctypes import wintypes
import importlib.util
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location('hc_linux', HERE / 'test-high-contrast-live.py')
_linux = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_linux)
MEASURE, CHROME, NEWTAB, MIN_RATIO = _linux.MEASURE, _linux.CHROME, _linux.NEWTAB, _linux.MIN_RATIO

SPI_GETHIGHCONTRAST, SPI_SETHIGHCONTRAST = 0x42, 0x43
HCF_HIGHCONTRASTON = 0x1
SPIF_UPDATEINIFILE_SENDCHANGE = 0x3


class HIGHCONTRASTW(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwFlags', wintypes.DWORD), ('lpszDefaultScheme', wintypes.LPWSTR)]


def high_contrast():
    hc = HIGHCONTRASTW(ctypes.sizeof(HIGHCONTRASTW), 0, None)
    if not ctypes.windll.user32.SystemParametersInfoW(SPI_GETHIGHCONTRAST, hc.cbSize, ctypes.byref(hc), 0):
        raise ctypes.WinError()
    return bool(hc.dwFlags & HCF_HIGHCONTRASTON), hc.lpszDefaultScheme


def set_high_contrast(on):
    hc = HIGHCONTRASTW(ctypes.sizeof(HIGHCONTRASTW), HCF_HIGHCONTRASTON if on else 0,
                       'High Contrast Black' if on else None)
    if not ctypes.windll.user32.SystemParametersInfoW(SPI_SETHIGHCONTRAST, hc.cbSize, ctypes.byref(hc),
                                                      SPIF_UPDATEINIFILE_SENDCHANGE):
        raise ctypes.WinError()
    deadline = time.monotonic() + 30
    while high_contrast()[0] != on:
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Windows high contrast did not become {on}')
        time.sleep(.5)
    time.sleep(5)  # let the theme change reach every window before Firefox starts


def run(nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-high-contrast-win-', ignore_cleanup_errors=True) as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.newtabpage.activity-stream.testing.shouldInitializeFeeds': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        process = subprocess.Popen([os.environ.get('FIREFOX', 'firefox'), '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env={**os.environ, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS': '1'},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script(
                "return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path, version:Services.appinfo.version, "
                "build:Services.appinfo.appBuildID, os:Services.appinfo.OS, "
                "nova:Services.prefs.getBoolPref('browser.nova.enabled'), "
                "colorUse:Services.prefs.getIntPref('browser.display.document_color_use'), "
                "colorUseSet:Services.prefs.prefHasUserValue('browser.display.document_color_use'), "
                "a11yThemeSet:Services.prefs.prefHasUserValue('ui.useAccessibilityTheme'), "
                "headless:Services.env.get('MOZ_HEADLESS')}",
                sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve()
                    or meta['nova'] != nova or meta['headless'] or meta['os'] != 'WINNT'
                    or meta['colorUse'] != 0 or meta['colorUseSet'] or meta['a11yThemeSet']):
                raise RuntimeError(f'unexpected Firefox process, profile, preference or platform: {meta}')
            result = {'contrastMore': m.execute_script("return matchMedia('(prefers-contrast: more)').matches"),
                      'chromeForced': m.execute_script("return matchMedia('(forced-colors: active)').matches"),
                      'chrome': m.execute_script(MEASURE, script_args=[CHROME])}
            m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox='system')
            m.set_context('content')
            m.switch_to_window(m.window_handles[-1])
            deadline = time.monotonic() + 30
            while not m.execute_script("return !!document.querySelector('.top-site-outer .title')"):
                if time.monotonic() >= deadline:
                    raise RuntimeError('new-tab shortcuts did not initialize')
                time.sleep(.25)
            result['contentForced'] = m.execute_script("return matchMedia('(forced-colors: active)').matches")
            result['newtab'] = m.execute_script(MEASURE, script_args=[NEWTAB])
            return meta, result
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                # terminate() stops only the parent; Firefox's child processes would
                # keep the profile's files open. End the whole tree.
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    failures = []
    initial = high_contrast()[0]
    try:
        for on in (False, True):
            set_high_contrast(on)
            scheme = high_contrast()[1]
            for nova in (False, True):
                for themed in (False, True):
                    label = f'highContrast={on} nova={nova} themed={themed}'
                    try:
                        meta, result = run(nova, themed)
                    except Exception as error:
                        failures.append(f'{label}: {error}')
                        continue
                    print(json.dumps({'firefox': meta, 'highContrast': on, 'scheme': scheme, 'nova': nova,
                                      'themed': themed, 'result': result}), flush=True)
                    if result['contrastMore'] != on:
                        failures.append(f'{label}: prefers-contrast more is {result["contrastMore"]}')
                    if result['chromeForced']:
                        failures.append(f'{label}: browser chrome reports forced colors')
                    if result['contentForced'] != on:
                        failures.append(f'{label}: new-tab forced colors is {result["contentForced"]}')
                    for area in ('chrome', 'newtab'):
                        for name, sample in result[area].items():
                            ratio = sample.get('ratio')
                            if ratio is None:
                                failures.append(f'{label}: {area} {name} not measurable: {sample}')
                            elif ratio < MIN_RATIO:
                                failures.append(f'{label}: {area} {name} contrast {ratio} < {MIN_RATIO}')
    finally:
        set_high_contrast(initial)
    if failures:
        for failure in failures:
            print(f'FAIL {failure}', file=sys.stderr)
        sys.exit(1)
    print('Windows high-contrast text contrast holds for 8 cases')


if __name__ == '__main__':
    main()
