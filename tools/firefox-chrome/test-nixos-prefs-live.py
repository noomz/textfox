#!/usr/bin/env python3
"""Run the real NixOS delivery in a disposable profile Firefox has already used.

The NixOS path disables Nova from `defaults/pref/autoconfig.js`, not from a
`user.js`, so the question this answers is whether an autoconfig `pref()` beats a
`browser.nova.enabled` value the profile already persisted. The profile seeded
here holds Nova enabled and a sentinel preference in `prefs.js`, plus unrelated
files. Firefox is the wrapped binary the module installs, run under nix-portable;
nothing is installed into a system.

Three launches. The first is the install run: the autoconfig copies the generated
CSS and restarts Firefox once, by design. Marionette is not carried into that
restarted process, and nix-portable's bwrap runtime ends a forked child when the
launched process exits, so this run keeps the sandbox alive with a trailing
`sleep`, then stops the restarted Firefox (the profile lock's owner) with
SIGTERM. The second launch finds the CSS current, does not restart, and is read
live over Marionette. The third reads only what Firefox persisted after the
second shutdown, so a pass means the migration holds across restarts. A
`user.js` left behind would weaken the claim, so the profile asserts it has none.

Requires the flake's nixpkgs source to be cached (no network) and cached
firefox-unwrapped. This is not a NixOS activation test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'tools/firefox-chrome/nix-portable-run.sh'
SENTINEL_PREF = 'user_pref("tf.test.sentinel", true);'
NOVA_PREFS = ('browser.nova.enabled', 'tf.test.sentinel')


def run_nix(args, env, timeout=600, capture=True):
    return subprocess.run(['sh', str(RUNNER), 'nix', *args], cwd=ROOT, env=env, text=capture,
                          capture_output=capture, timeout=timeout)


def build(env, nixpkgs_path):
    if not re.fullmatch(r'/nix/store/[a-zA-Z0-9._/-]+', nixpkgs_path):
        raise SystemExit('nixpkgs_path must be a literal /nix/store path')
    expression = (f'import ./tools/firefox-chrome/check-nixos-prefs.nix '
                  f'{{ nixpkgsPath = {nixpkgs_path}; }}')
    result = run_nix(['build', '--offline', '--impure', '--no-link', '--expr',
                      expression, '--print-out-paths'], env)
    if result.returncode != 0:
        raise RuntimeError(f'building the wrapped Firefox failed:\n{result.stdout}{result.stderr}')
    return Path(result.stdout.strip().splitlines()[-1])


@contextmanager
def session(store, env, profile, port):
    process = subprocess.Popen(
        ['sh', str(RUNNER), str(store), 'firefox', '--marionette', '-remote-allow-system-access', '--new-instance',
         '--profile', str(profile), 'about:blank'],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    m = None
    try:
        m = connect(port)
        m.set_context('chrome')
        yield m, process
    finally:
        try:
            if m:
                try:
                    m.delete_session()
                except Exception:  # noqa: BLE001 - session may already be gone
                    pass
            # Marionette's quit() refuses a Firefox it did not launch, and
            # signalling the nix-portable wrapper does not reach Firefox, so the
            # profile lock's owner is stopped directly.
            stop_firefox(profile)
        finally:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def lock_owner(profile):
    """The pid in Firefox's profile lock symlink (`<address>:+<pid>`), or None."""
    try:
        target = os.readlink(profile / 'lock')
    except OSError:
        return None
    pid = target.rpartition('+')[2]
    return int(pid) if pid.isdigit() else None


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stop_firefox(profile):
    """SIGTERM the Firefox that holds the profile lock; wait for a clean release."""
    owner = lock_owner(profile)
    if owner is None or not alive(owner):
        raise RuntimeError('no running Firefox holds the profile lock')
    os.kill(owner, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while alive(owner):
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Firefox {owner} did not exit on SIGTERM')
        time.sleep(.2)
    if lock_owner(profile) is not None:
        raise RuntimeError('Firefox left its profile lock behind')


def install_run(store, env, profile, settle=8):
    """Let the autoconfig install and restart once, then stop the restarted Firefox."""
    marker = profile / 'chrome' / 'textfox-installed.json'
    process = subprocess.Popen(
        ['sh', str(RUNNER), str(store), 'sh', '-c',
         'firefox --new-instance --profile "$1" about:blank; exec sleep 600', 'sh', str(profile)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        deadline = time.monotonic() + 60
        while not marker.is_file():
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError('the autoconfig never wrote its install marker')
            time.sleep(.2)
        # The restarted process takes the lock after the installing one quits;
        # waiting out a fixed settle period lets the owner become that process.
        time.sleep(settle)
        stop_firefox(profile)
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.wait(timeout=30)


def read_state(m):
    return m.execute_script("""
        const prefs = Services.prefs, defaults = prefs.getDefaultBranch('');
        const read = (branch, name) => { try { return branch.getBoolPref(name); } catch (_) { return null; } };
        const nav = getComputedStyle(document.getElementById('nav-bar'));
        return {
          profile: Services.dirsvc.get('ProfD', Ci.nsIFile).path,
          version: Services.appinfo.version,
          preferences: Object.fromEntries(arguments[0].map(n => [n, {
            effective: read(prefs, n), default: read(defaults, n), user: prefs.prefHasUserValue(n)
          }])),
          navbarBorderWidth: nav.borderTopWidth
        };
    """, script_args=[list(NOVA_PREFS)], sandbox='system')


def wait_started(m):
    deadline = time.monotonic() + 30
    while not m.execute_script('return gBrowserInit.delayedStartupFinished', sandbox='system'):
        if time.monotonic() >= deadline:
            raise RuntimeError('Firefox startup did not finish')
        time.sleep(.1)


def persisted_prefs(profile):
    """The values Firefox wrote to prefs.js after the previous session shut down."""
    text = (profile / 'prefs.js').read_text()
    values = {name: None for name in NOVA_PREFS}
    for name in values:
        for line in text.splitlines():
            if line.startswith(f'user_pref("{name}", '):
                values[name] = line.rstrip().endswith('true);')
    return values


def seed(profile, port):
    (profile / 'prefs.js').write_text(
        '// Mozilla User Preferences\n'
        'user_pref("browser.nova.enabled", true);\n'
        'user_pref("browser.newtabpage.activity-stream.nova.enabled", true);\n'
        f'{SENTINEL_PREF}\n'
        f'user_pref("marionette.port", {port});\n'
        'user_pref("browser.shell.checkDefaultBrowser", false);\n')
    (profile / 'unrelated.txt').write_text('preserve this profile file')


def assert_state(label, state, profile):
    if state['profile'] != str(profile):
        raise RuntimeError(f"{label}: unexpected profile {state['profile']}")
    nova = state['preferences']['browser.nova.enabled']
    sentinel = state['preferences']['tf.test.sentinel']
    if nova['effective'] is not False or nova['user'] is not True:
        raise RuntimeError(f'{label}: autoconfig did not disable Nova over the persisted value: {nova}')
    if sentinel['effective'] is not True:
        raise RuntimeError(f"{label}: the profile's own preference stopped applying: {sentinel}")
    if state['navbarBorderWidth'] != '11px':
        raise RuntimeError(f"{label}: the generated CSS was not installed: {state['navbarBorderWidth']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('nixpkgs_path')
    args = parser.parse_args()

    env = os.environ.copy()
    env['NP_RUNTIME'] = 'bwrap'
    env['MOZ_HEADLESS'] = '1'
    env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
    store = build(env, args.nixpkgs_path)

    with tempfile.TemporaryDirectory(prefix='tf-nixos-prefs-') as directory:
        profile = Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        seed(profile, port)
        install_run(store, env, profile)
        print('install run: CSS installed, restarted Firefox stopped', flush=True)

        with session(store, env, profile, port) as (m, process):
            wait_started(m)
            first = read_state(m)
            if process.poll() is not None:
                raise RuntimeError('Firefox exited during the preference read')
            assert_state('first live start', first, profile)
        print('first live start: Nova off, sentinel kept, CSS applied', flush=True)

        if (profile / 'user.js').exists():
            raise RuntimeError('a user.js appeared: the autoconfig path must not write one')
        persisted = persisted_prefs(profile)
        if persisted['browser.nova.enabled'] is not False:
            raise RuntimeError(f'prefs.js did not persist the migrated Nova value: {persisted}')
        if persisted['tf.test.sentinel'] is not True:
            raise RuntimeError(f"prefs.js dropped the profile's own preference: {persisted}")

        with session(store, env, profile, port) as (m, process):
            wait_started(m)
            second = read_state(m)
            if process.poll() is not None:
                raise RuntimeError('Firefox exited during the preference read')
            assert_state('restart from persisted prefs', second, profile)

        if (profile / 'unrelated.txt').read_text() != 'preserve this profile file':
            raise RuntimeError('the autoconfig install changed an unrelated profile file')
        if not (profile / 'chrome' / 'userChrome.css').is_file():
            raise RuntimeError('the autoconfig install did not write the generated CSS')
        print(json.dumps({'case': 'nixos-autoconfig-existing-profile', 'store': str(store),
                          'firefox': first['version'], 'prefs.js': persisted,
                          'border': first['navbarBorderWidth']}), flush=True)
    print('nixos autoconfig preference migration holds across restarts')


if __name__ == '__main__':
    main()
