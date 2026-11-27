#!/usr/bin/env python3
"""Exercise manual installation in disposable Firefox profiles.

Two shapes: a fresh profile, and a profile that already has preferences of its
own (`prefs.js` enabling Nova, plus a `user.js` the user wrote). V3 requires both
to reach the intended preference state through the delivered migration, and V2
requires the user's own settings to keep applying. The existing-profile case
launches Firefox twice, so the result is a persisted state rather than a
first-read accident.

Since T25 textfox assigns no Nova value: a fresh install leaves Firefox's
default, and a Nova value the user chose before textfox stays. Clearing the false
an earlier textfox persisted is `test-clear-nova-live.py`'s manual case.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
SENTINEL_PREF = 'user_pref("tf.test.sentinel", true);'
USER_PREF = 'user_pref("tf.test.userjs", true);'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def install(profile):
    result = subprocess.run([os.environ.get('TF_TEST_SHELL', 'bash'), str(ROOT / 'tf-install.sh'), str(profile)], cwd=ROOT,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or 'Installation completed' not in result.stdout:
        raise RuntimeError(f'manual installer failed: {result.stdout} {result.stderr}')


@contextmanager
def session(profile, port):
    env = os.environ.copy()
    env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
    process = subprocess.Popen([os.environ.get('FIREFOX', 'firefox'), '--marionette', '-remote-allow-system-access',
                                '--new-instance', '--profile', str(profile), 'about:blank'],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    m = None
    try:
        m = connect(port)
        m.set_context('chrome')
        yield m, process
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


def read_preferences(m, profile, port):
    meta = m.execute_script("""
        const prefs = Services.prefs, defaults = prefs.getDefaultBranch('');
        const read = (branch, name) => { try { return branch.getBoolPref(name); } catch (_) { return null; } };
        const names = arguments[0];
        return {
          profile: Services.dirsvc.get('ProfD', Ci.nsIFile).path,
          version: Services.appinfo.version,
          build: Services.appinfo.appBuildID,
          preferences: Object.fromEntries(names.map(n => [n, {
            effective: read(prefs, n), default: read(defaults, n), user: prefs.prefHasUserValue(n)
          }]))
        };
    """, script_args=[['browser.nova.enabled', 'tf.test.sentinel', 'tf.test.userjs']],
        sandbox='system')
    if meta['profile'] != str(profile):
        raise RuntimeError(f"unexpected Firefox profile: {meta['profile']}")
    return meta


def check(browser_styles, profile, port):
    with session(profile, port) as (m, process):
        meta = read_preferences(m, profile, port)
        if process.poll() is not None:
            raise RuntimeError('Firefox exited during the preferences read')
        deadline = time.monotonic() + 20
        while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
            if time.monotonic() >= deadline:
                raise RuntimeError('Firefox startup did not finish')
            time.sleep(.1)
        m.execute_script("gURLBar.focus(); gURLBar.search('mozilla')", sandbox='system')
        deadline = time.monotonic() + 20
        while not m.execute_script("return !!document.querySelector('#urlbar[open] .urlbarView-results .urlbarView-row')", sandbox='system'):
            if time.monotonic() >= deadline:
                raise RuntimeError('manual-install URL bar results did not open')
            time.sleep(.1)
        result = m.execute_script("""
            const s = getComputedStyle(document.querySelector('#urlbar[open] .urlbarView'));
            return {borderWidth:s.borderLeftWidth, borderColor:s.borderLeftColor, dividerWidth:s.borderTopWidth};
            """, sandbox='system')
        print(json.dumps({'case': 'manual', 'firefox': meta, 'urlbar': result}), flush=True)
        if browser_styles and result != {'borderWidth': '3px', 'borderColor': 'rgb(19, 83, 137)',
                                         'dividerWidth': '1px'}:
            raise RuntimeError('manual installation did not honor configured URL bar styles')
        m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:preferences")', sandbox='system')
        m.set_context('content')
        m.switch_to_window(m.window_handles[-1])
        deadline = time.monotonic() + 20
        while not m.execute_script('return location.href.startsWith("about:preferences") && !!document.querySelector("body")'):
            if time.monotonic() >= deadline:
                raise RuntimeError('preferences page did not load')
            time.sleep(.1)
        font = m.execute_script("return getComputedStyle(document.body).fontFamily")
        if font != 'monospace':
            raise RuntimeError(f'manually installed about-page font not applied: {font}')
        print(json.dumps({'case': 'manual', 'aboutPreferencesFont': font}), flush=True)
    return meta


def configure(profile, port):
    (profile / 'chrome' / 'config.css').write_text(
        ':root { --tf-accent: rgb(19, 83, 137); --tf-border-width: 3px; '
        '--tf-font-family: monospace; }\n')
    with (profile / 'user.js').open('a') as prefs:
        prefs.write(f'user_pref("marionette.port", {port});\n')
        prefs.write('user_pref("browser.shell.checkDefaultBrowser", false);\n')


def fresh_profile():
    with tempfile.TemporaryDirectory(prefix='tf-manual-install-fresh-') as directory:
        profile = Path(directory)
        (profile / 'unrelated.txt').write_text('preserve this profile file')
        install(profile)
        if (profile / 'unrelated.txt').read_text() != 'preserve this profile file':
            raise RuntimeError('manual installer changed unrelated profile file')
        if not (profile / 'chrome' / 'icons').is_dir():
            raise RuntimeError('manual installation did not copy icons')
        if not (profile / 'user.js').is_file():
            raise RuntimeError('manual installation did not write the preference block')
        port = free_port()
        configure(profile, port)
        meta = check(True, profile, port)
        nova = meta['preferences']['browser.nova.enabled']
        if nova['user'] or nova['effective'] != nova['default']:
            raise RuntimeError(f'fresh install gave Nova a value instead of Firefox\'s default: {nova}')
        print(json.dumps({'case': 'fresh-profile passed'}), flush=True)


def existing_profile():
    """A profile that already ran Firefox and holds the user's own preferences."""
    with tempfile.TemporaryDirectory(prefix='tf-manual-install-existing-') as directory:
        profile = Path(directory)
        (profile / 'prefs.js').write_text(
            '// Mozilla User Preferences\n'
            'user_pref("browser.nova.enabled", true);\n'
            'user_pref("browser.newtabpage.activity-stream.nova.enabled", true);\n'
            f'{SENTINEL_PREF}\n')
        (profile / 'user.js').write_text('// my own settings\n' + USER_PREF + '\n')
        (profile / 'unrelated.txt').write_text('preserve this profile file')
        install(profile)

        if (profile / 'unrelated.txt').read_text() != 'preserve this profile file':
            raise RuntimeError('manual installer changed unrelated profile file')
        user_js = (profile / 'user.js').read_text()
        if USER_PREF not in user_js:
            raise RuntimeError('manual installer removed the user\'s own user.js preference')
        if user_js.count('// >>> textfox managed prefs >>>') != 1:
            raise RuntimeError('manual installer did not write exactly one managed block')
        if not sorted(profile.glob('user.js-*.bak')):
            raise RuntimeError('replacing the user.js preference block left no backup')

        port = free_port()
        configure(profile, port)
        # Two launches: the second one reads the state Firefox persisted, so the
        # assertion is about migration, not about a first-start read.
        first = check(True, profile, port)
        persisted = read_prefs_js(profile, first)
        second = check(False, profile, port)
        for label, meta in (('first start', first), ('second start', second)):
            nova = meta['preferences']['browser.nova.enabled']
            sentinel = meta['preferences']['tf.test.sentinel']
            user_pref = meta['preferences']['tf.test.userjs']
            # Firefox drops a user value equal to the default when it saves, so
            # only the effective value is the user's choice here.
            if nova['effective'] is not True:
                raise RuntimeError(f'{label}: the user\'s own Nova true did not stay: {nova}')
            if sentinel['effective'] is not True:
                raise RuntimeError(f'{label}: the profile lost its own preference: {sentinel}')
            if user_pref['effective'] is not True:
                raise RuntimeError(f'{label}: the user\'s user.js preference stopped applying: {user_pref}')
        if persisted['browser.nova.enabled'] is False:
            raise RuntimeError(f'prefs.js did not keep the user\'s Nova value: {persisted}')
        if persisted['tf.test.sentinel'] is not True:
            raise RuntimeError(f'prefs.js dropped the user\'s own preference: {persisted}')
        print(json.dumps({'case': 'existing-profile passed', 'prefs.js': persisted}), flush=True)


def read_prefs_js(profile, meta):
    """Read the values Firefox persisted after the first session shut down."""
    text = (profile / 'prefs.js').read_text()
    values = {'browser.nova.enabled': None, 'tf.test.sentinel': None}
    for name in values:
        for line in text.splitlines():
            if line.startswith(f'user_pref("{name}", '):
                values[name] = line.rstrip().endswith('true);')
    return values


if __name__ == '__main__':
    fresh_profile()
    existing_profile()
