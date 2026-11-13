#!/usr/bin/env python3
"""Prove the retirement clear of `browser.nova.enabled` on disposable profiles (T19).

Retiring textfox's Nova assignment must hand an existing profile back Firefox's
default (V3) without losing the user's own preferences (V2). `user.js` cannot
clear a preference, so the manual and home-manager paths run
`tools/clear-nova-pref.py` outside Firefox. Nothing here changes what textfox
ships; the retired `user.js` block is simulated in the disposable profiles.

File cases run without Firefox. Live cases use a real Firefox:

- manual: a profile installed with `tf-install.sh`, started once so Firefox itself
  persisted Nova false, then given a retired block. Removing the assignment alone
  must leave Nova false (the control). The clear must refuse while Firefox runs,
  then clear, be one-shot, and survive two later starts.
- user value: afterwards the user sets Nova false in their own `user.js` lines;
  the marker keeps the clear from undoing it.
- home-manager: `user.js` is a read-only symlink swapped between two generations,
  the way home-manager links it. The clear must not touch the link.
- stale lock: Firefox killed with SIGKILL leaves its lock files behind; the clear
  must treat them as free, as Firefox does.

Run: MOZ_HEADLESS=1 "$HOME/.cache/textfox-firefox-live/venv/bin/python" tools/firefox-chrome/test-clear-nova-live.py
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[2]
CLEAR = Path(os.environ.get('TF_CLEAR_NOVA', ROOT / 'tools' / 'clear-nova-pref.py'))
NOVA = 'browser.nova.enabled'
MARKER = 'textfox.nova.cleared'
NOVA_LINE = 'user_pref("browser.nova.enabled", false);'
NOVA_COMMENT = '// Disable the new Firefox "Nova" UI (textfox does not support it yet)'
SENTINEL = 'user_pref("tf.test.sentinel", true);'
USER_JS_PREF = 'user_pref("tf.test.userjs", true);'
NAMES = [NOVA, MARKER, 'tf.test.sentinel', 'tf.test.userjs']


def fail(message):
    raise AssertionError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run_clear(profile):
    return subprocess.run([sys.executable, str(CLEAR), str(profile)],
                          capture_output=True, text=True, timeout=30)


def backups(profile):
    return sorted(profile.glob('prefs.js-*.textfox.bak'))


def prefs_lines(profile):
    return (profile / 'prefs.js').read_text().splitlines()


def has_line(profile, prefix):
    return any(line.startswith(prefix) for line in prefs_lines(profile))


# --- file cases -------------------------------------------------------------

def file_cases():
    with tempfile.TemporaryDirectory(prefix='tf-clear-nova-files-') as directory:
        base = Path(directory).resolve()

        bare = base / 'bare'
        bare.mkdir()
        result = run_clear(bare)
        if result.returncode != 1 or any(bare.iterdir()):
            fail(f'directory that is not a profile: expected refusal and no files, got {result.returncode}')

        fresh = base / 'fresh'
        fresh.mkdir()
        (fresh / 'times.json').write_text('{}')
        result = run_clear(fresh)
        if result.returncode != 0:
            fail(f'fresh profile: exit {result.returncode}: {result.stderr}')
        if (fresh / 'prefs.js').read_text() != f'user_pref("{MARKER}", true);\n':
            fail('fresh profile: prefs.js is not the marker alone')
        if (fresh / 'prefs.js').stat().st_mode & 0o777 != 0o600 or backups(fresh):
            fail('fresh profile: wrong mode or unexpected backup')

        existing = base / 'existing'
        existing.mkdir()
        original = ('// Mozilla User Preferences\n'
                    'user_pref("browser.newtabpage.activity-stream.nova.enabled", false);\n'
                    f'{NOVA_LINE}\n'
                    '  user_pref( "browser.nova.enabled" , false );\n'
                    'user_pref("tf.test.text", "a\u2028user_pref(\\"browser.nova.enabled\\", false);");\n'
                    f'{SENTINEL}')  # no trailing newline
        (existing / 'prefs.js').write_text(original)
        os.chmod(existing / 'prefs.js', 0o640)
        (existing / 'prefs.js.textfox-stale').write_text('left by a killed run')
        result = run_clear(existing)
        if result.returncode != 0:
            fail(f'existing profile: exit {result.returncode}: {result.stderr}')
        expected = ('// Mozilla User Preferences\n'
                    'user_pref("browser.newtabpage.activity-stream.nova.enabled", false);\n'
                    'user_pref("tf.test.text", "a\u2028user_pref(\\"browser.nova.enabled\\", false);");\n'
                    f'{SENTINEL}\n'
                    f'user_pref("{MARKER}", true);\n')
        if (existing / 'prefs.js').read_text() != expected:
            fail(f'existing profile: unexpected prefs.js:\n{(existing / "prefs.js").read_text()}')
        if (existing / 'prefs.js').stat().st_mode & 0o777 != 0o640:
            fail('existing profile: prefs.js mode not preserved')
        if (existing / 'prefs.js.textfox-stale').exists():
            fail('existing profile: temp file of a killed run not swept')
        saved = backups(existing)
        if len(saved) != 1 or saved[0].read_text() != original:
            fail('existing profile: backup missing or not the original bytes')

        before = (existing / 'prefs.js').read_bytes()
        result = run_clear(existing)
        if result.returncode != 0 or 'already cleared' not in result.stdout:
            fail(f'second run: not a no-op: {result.returncode} {result.stdout} {result.stderr}')
        if (existing / 'prefs.js').read_bytes() != before or len(backups(existing)) != 1:
            fail('second run: changed prefs.js or wrote another backup')

        # Kept values: a true is not textfox's (Firefox drops a user value equal
        # to the default), and a user.js value outside the textfox block is the user's.
        own = base / 'own'
        own.mkdir()
        (own / 'prefs.js').write_text('user_pref("browser.nova.enabled", true);\n')
        (own / 'user.js').write_text(f'{NOVA_LINE}\n' + (ROOT / 'user.js').read_text().replace(f'{NOVA_LINE}\n', ''))
        result = run_clear(own)
        if result.returncode != 0 or not (own / 'prefs.js').read_text().startswith('user_pref("browser.nova.enabled", true);\n'):
            fail(f'own values: expected the true kept, got {result.returncode}: {(own / "prefs.js").read_text()}')

        # Order safety: a block that still assigns Nova would write it back after the marker.
        early = base / 'early'
        early.mkdir()
        (early / 'prefs.js').write_text(f'{NOVA_LINE}\n')
        (base / 'early-user.js').write_text((ROOT / 'user.js').read_text())
        (early / 'user.js').symlink_to(base / 'early-user.js')
        result = run_clear(early)
        if result.returncode != 1 or 'still assigns' not in result.stderr:
            fail(f'unretired block: expected refusal, got {result.returncode}: {result.stderr}')
        if (early / 'prefs.js').read_text() != f'{NOVA_LINE}\n' or backups(early):
            fail('unretired block: prefs.js changed')

        # Self-consistency only: the helper's lock against itself. The live cases
        # check the lock against Firefox in both directions.
        locked = base / 'locked'
        locked.mkdir()
        (locked / 'prefs.js').write_text(f'{NOVA_LINE}\n')
        holder = lock_holder(locked)
        try:
            result = run_clear(locked)
        finally:
            holder.kill()
            holder.wait()
        if result.returncode != 75:
            fail(f'held lock: expected exit 75, got {result.returncode}')
        if (locked / 'prefs.js').read_text() != f'{NOVA_LINE}\n' or backups(locked):
            fail('held lock: prefs.js changed or backup written')

        linked = base / 'linked'
        linked.mkdir()
        (base / 'real-prefs.js').write_text(f'{NOVA_LINE}\n')
        (linked / 'prefs.js').symlink_to(base / 'real-prefs.js')
        result = run_clear(linked)
        if result.returncode != 1 or not (linked / 'prefs.js').is_symlink():
            fail(f'symlinked prefs.js: expected refusal, got {result.returncode}')
        if (base / 'real-prefs.js').read_text() != f'{NOVA_LINE}\n':
            fail('symlinked prefs.js: target changed')
    print(json.dumps({'case': 'file cases passed', 'count': 8}), flush=True)


def lock_holder(profile):
    """A process holding `.parentlock` the way clear-nova-pref.py does."""
    holder = subprocess.Popen([sys.executable, '-c', (
        'import fcntl, os, sys, time\n'
        'fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o644)\n'
        'fcntl.lockf(fd, fcntl.LOCK_EX)\n'
        'print("held", flush=True)\n'
        'time.sleep(120)\n'), str(profile / '.parentlock')], stdout=subprocess.PIPE, text=True)
    if holder.stdout.readline().strip() != 'held':
        holder.kill()
        fail('lock holder did not start')
    return holder


# --- live Firefox -----------------------------------------------------------

def connect(port):
    from ff_inspect import connect as ff_connect
    return ff_connect(port)


@contextmanager
def firefox(profile, port):
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
        # Quit from inside Firefox so it saves prefs.js the way a user's quit does;
        # a SIGTERM can end macOS Firefox before a pending save. SIGTERM stays the
        # fallback when the session is gone.
        try:
            if m and process.poll() is None:
                m.execute_script('Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit)', sandbox='system')
        except Exception:  # noqa: BLE001 - the quit drops the connection
            pass
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def read_state(m, profile):
    state = m.execute_script("""
        const prefs = Services.prefs, defaults = prefs.getDefaultBranch('');
        const read = (branch, name) => { try { return branch.getBoolPref(name); } catch (_) { return null; } };
        return {
          profile: Services.dirsvc.get('ProfD', Ci.nsIFile).path,
          version: Services.appinfo.version,
          build: Services.appinfo.appBuildID,
          preferences: Object.fromEntries(arguments[0].map(n => [n, {
            effective: read(prefs, n), default: read(defaults, n), user: prefs.prefHasUserValue(n)
          }]))
        };
    """, script_args=[NAMES], sandbox='system')
    if state['profile'] != str(profile):
        fail(f"unexpected Firefox profile: {state['profile']}")
    return state


def start_and_read(profile, port):
    with firefox(profile, port) as (m, _):
        return read_state(m, profile)


def expect_default(label, state):
    nova = state['preferences'][NOVA]
    if nova['default'] is not True:
        fail(f'{label}: Firefox default for {NOVA} is not true; this check assumes 159: {nova}')
    if nova['user'] is not False or nova['effective'] != nova['default']:
        fail(f'{label}: Nova did not return to the Firefox default: {nova}')


def expect_textfox_false(label, state):
    nova = state['preferences'][NOVA]
    if nova['effective'] is not False or nova['user'] is not True:
        fail(f'{label}: Nova is not false as a user value: {nova}')


def retire_block(user_js):
    """The block a retired textfox would ship: the same, without the Nova assignment."""
    text = user_js.read_text()
    if NOVA_LINE not in text or NOVA_COMMENT not in text:
        fail('shipped user.js no longer carries the Nova assignment this test retires')
    user_js.write_text(text.replace(f'{NOVA_COMMENT}\n{NOVA_LINE}\n\n', ''))
    if NOVA_LINE in user_js.read_text():
        fail('retired block still assigns Nova')


def manual_case():
    with tempfile.TemporaryDirectory(prefix='tf-clear-nova-manual-') as directory:
        profile = Path(directory).resolve()
        port = free_port()
        (profile / 'prefs.js').write_text(f'// Mozilla User Preferences\n{SENTINEL}\n')
        (profile / 'user.js').write_text(
            f'// my own settings\n{USER_JS_PREF}\nuser_pref("marionette.port", {port});\n'
            'user_pref("browser.shell.checkDefaultBrowser", false);\n')
        (profile / 'unrelated.txt').write_text('preserve this profile file')
        install = subprocess.run(['bash', str(ROOT / 'tf-install.sh'), str(profile)], cwd=ROOT,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        if install.returncode != 0:
            fail(f'tf-install.sh failed: {install.stdout} {install.stderr}')

        installed = start_and_read(profile, port)
        expect_textfox_false('installed', installed)
        if not has_line(profile, NOVA_LINE):
            fail('Firefox did not persist textfox\'s Nova value in prefs.js')
        early = run_clear(profile)
        if early.returncode != 1 or not has_line(profile, NOVA_LINE) or backups(profile):
            fail(f'clear before the retired block: expected refusal, got {early.returncode}')

        retire_block(profile / 'user.js')
        control = start_and_read(profile, port)
        expect_textfox_false('control (assignment removed, no clear)', control)

        with firefox(profile, port) as (m, _):
            before = (profile / 'prefs.js').read_bytes()
            refused = run_clear(profile)
            if refused.returncode != 75:
                fail(f'clear while Firefox runs: expected exit 75, got {refused.returncode}: {refused.stdout}')
            if (profile / 'prefs.js').read_bytes() != before or backups(profile):
                fail('clear while Firefox runs touched prefs.js')
            read_state(m, profile)

        # The other direction: Firefox must refuse a profile the helper holds.
        before = (profile / 'prefs.js').read_bytes()
        holder = lock_holder(profile)
        try:
            env = os.environ.copy()
            env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
            blocked = subprocess.run([os.environ.get('FIREFOX', 'firefox'), '--marionette', '-remote-allow-system-access',
                                      '--new-instance', '--profile', str(profile), 'about:blank'],
                                     env=env, capture_output=True, text=True, timeout=60)
        finally:
            holder.kill()
            holder.wait()
        # Linux: "Firefox is already running"; macOS: "A copy of Firefox is already open".
        if not any(text in blocked.stdout + blocked.stderr for text in ('already running', 'already open')):
            fail(f'Firefox did not refuse the locked profile: {blocked.returncode} {blocked.stderr[-300:]}')
        if (profile / 'prefs.js').read_bytes() != before:
            fail('Firefox wrote prefs.js while the helper held the lock')

        cleared = run_clear(profile)
        if cleared.returncode != 0:
            fail(f'clear failed: {cleared.returncode} {cleared.stderr}')
        if has_line(profile, 'user_pref("browser.nova.enabled"'):
            fail('clear left a Nova line in prefs.js')
        if not has_line(profile, SENTINEL) or not has_line(profile, f'user_pref("{MARKER}", true);'):
            fail('clear dropped the sentinel or did not mark')
        snapshot = (profile / 'prefs.js').read_bytes()
        again = run_clear(profile)
        if again.returncode != 0 or (profile / 'prefs.js').read_bytes() != snapshot or len(backups(profile)) != 1:
            fail('clear is not one-shot')

        first = start_and_read(profile, port)
        second = start_and_read(profile, port)
        for label, state in (('first start after clear', first), ('second start after clear', second)):
            expect_default(label, state)
            prefs = state['preferences']
            if prefs['tf.test.sentinel']['effective'] is not True or prefs['tf.test.userjs']['effective'] is not True:
                fail(f'{label}: the user\'s preferences did not survive: {prefs}')
            if prefs[MARKER]['effective'] is not True:
                fail(f'{label}: marker lost: {prefs[MARKER]}')
        if has_line(profile, 'user_pref("browser.nova.enabled"'):
            fail('Firefox persisted a Nova user value after the clear')
        if not has_line(profile, f'user_pref("{MARKER}", true);') or not has_line(profile, SENTINEL):
            fail('Firefox did not persist the marker and sentinel')
        if (profile / 'unrelated.txt').read_text() != 'preserve this profile file':
            fail('unrelated profile file changed')
        print(json.dumps({'case': 'manual passed', 'firefox': [first['version'], first['build']],
                          'installed': installed['preferences'][NOVA], 'control': control['preferences'][NOVA],
                          'after': second['preferences'][NOVA]}), flush=True)

        # The user's own choice, made after the clear, must stick.
        with (profile / 'user.js').open('r+') as handle:
            text = handle.read()
            handle.seek(0)
            handle.write(f'{NOVA_LINE}\n{text}')
        rerun = run_clear(profile)
        if rerun.returncode != 0 or 'already cleared' not in rerun.stdout:
            fail(f'marked profile was cleared again: {rerun.stdout}')
        own = start_and_read(profile, port)
        expect_textfox_false('user\'s own value', own)
        if run_clear(profile).returncode != 0 or not has_line(profile, NOVA_LINE):
            fail('clear removed the user\'s own persisted Nova value')
        print(json.dumps({'case': 'user value passed', 'nova': own['preferences'][NOVA]}), flush=True)


def home_manager_case():
    with tempfile.TemporaryDirectory(prefix='tf-clear-nova-hm-') as directory:
        base = Path(directory).resolve()
        profile = base / 'profile'
        profile.mkdir()
        port = free_port()
        shipped = (ROOT / 'user.js').read_text()
        extra = f'\nuser_pref("marionette.port", {port});\nuser_pref("browser.shell.checkDefaultBrowser", false);\n'
        old, new = base / 'gen-old-user.js', base / 'gen-new-user.js'
        old.write_text(shipped + extra)
        new.write_text(shipped + extra)
        retire_block(new)
        for generation in (old, new):
            os.chmod(generation, 0o444)
        (profile / 'user.js').symlink_to(old)
        (profile / 'prefs.js').write_text(f'// Mozilla User Preferences\n{SENTINEL}\n')

        installed = start_and_read(profile, port)
        expect_textfox_false('home-manager installed', installed)
        if not has_line(profile, NOVA_LINE):
            fail('home-manager: Firefox did not persist textfox\'s Nova value')
        (profile / 'user.js').unlink()
        (profile / 'user.js').symlink_to(new)
        control = start_and_read(profile, port)
        expect_textfox_false('home-manager control (new generation, no clear)', control)

        cleared = run_clear(profile)
        if cleared.returncode != 0:
            fail(f'home-manager clear failed: {cleared.returncode} {cleared.stderr}')
        if os.readlink(profile / 'user.js') != str(new) or new.read_text() != (shipped + extra).replace(
                f'{NOVA_COMMENT}\n{NOVA_LINE}\n\n', ''):
            fail('home-manager clear touched the linked user.js')
        after = start_and_read(profile, port)
        expect_default('home-manager after clear', after)
        if after['preferences']['tf.test.sentinel']['effective'] is not True:
            fail('home-manager: sentinel lost')
        print(json.dumps({'case': 'home-manager passed', 'installed': installed['preferences'][NOVA],
                          'control': control['preferences'][NOVA],
                          'after': after['preferences'][NOVA]}), flush=True)


def stale_lock_case():
    with tempfile.TemporaryDirectory(prefix='tf-clear-nova-stale-') as directory:
        profile = Path(directory).resolve()
        port = free_port()
        (profile / 'prefs.js').write_text(f'// Mozilla User Preferences\n{NOVA_LINE}\n{SENTINEL}\n')
        (profile / 'user.js').write_text(f'user_pref("marionette.port", {port});\n'
                                         'user_pref("browser.shell.checkDefaultBrowser", false);\n')
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen([os.environ.get('FIREFOX', 'firefox'), '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            m = connect(port)
            m.set_context('chrome')
            read_state(m, profile)
        finally:
            process.send_signal(signal.SIGKILL)
            process.wait()
        # Linux Firefox leaves its `lock` symlink behind; macOS uses only the
        # fcntl lock on `.parentlock`, which the kernel drops with the process.
        if sys.platform == 'darwin':
            if not (profile / '.parentlock').exists():
                fail('Firefox left no .parentlock; the stale-lock case is not exercised')
            stale = '.parentlock'
        else:
            if not (profile / 'lock').is_symlink():
                fail('SIGKILL left no lock symlink; the stale-lock case is not exercised')
            stale = os.readlink(profile / 'lock')
        if not has_line(profile, NOVA_LINE):
            fail('stale lock: prefs.js lost the Nova line before the clear')
        cleared = run_clear(profile)
        if cleared.returncode != 0:
            fail(f'stale lock: expected the clear to proceed, got {cleared.returncode}: {cleared.stderr}')
        after = start_and_read(profile, port)
        expect_default('after stale lock', after)
        if after['preferences']['tf.test.sentinel']['effective'] is not True:
            fail('stale lock: sentinel lost')
        print(json.dumps({'case': 'stale lock passed', 'staleLock': stale}), flush=True)


if __name__ == '__main__':
    file_cases()
    manual_case()
    home_manager_case()
    stale_lock_case()
    print(json.dumps({'result': 'PASS'}))
