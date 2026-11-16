#!/usr/bin/env python3
"""Verify chrome inspection rejects an unexpected Firefox profile."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

from ff_inspect import connect

TOOL_DIR = Path(__file__).resolve().parent


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-inspector-') as directory:
        profile = Path(directory)
        (profile / 'user.js').write_text(
            f'user_pref("marionette.port", {json.dumps(port)});\n'
            'user_pref("browser.shell.checkDefaultBrowser", false);\n')
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            m = connect(port)
            m.set_context('chrome')
            actual = m.execute_script("return Services.dirsvc.get('ProfD', Ci.nsIFile).path", sandbox='system')
            if Path(actual).resolve() != profile.resolve() or process.poll() is not None:
                raise RuntimeError('unexpected Firefox process or profile')
            m.delete_session()
            base = [sys.executable, str(TOOL_DIR / 'ff_inspect.py'), '--port', str(port),
                    '--pid', str(process.pid), '--id', 'nav-bar', '--profile']
            wrong = subprocess.run(base + [str(profile / 'wrong')], capture_output=True, text=True, timeout=60)
            if wrong.returncode == 0 or 'unexpected Firefox profile' not in wrong.stderr:
                raise RuntimeError(f'wrong profile not rejected: {wrong.stdout} {wrong.stderr}')
            valid = subprocess.run(base + [str(profile)], capture_output=True, text=True, timeout=60)
            if valid.returncode != 0 or json.loads(valid.stdout).get('id') != 'nav-bar':
                raise RuntimeError(f'valid profile failed: {valid.stdout} {valid.stderr}')
            dead = subprocess.Popen([sys.executable, '-c', 'pass'])
            dead.wait()
            exited = subprocess.run(base + [str(profile), '--pid', str(dead.pid)],
                                    capture_output=True, text=True, timeout=60)
            if exited.returncode == 0 or 'launched Firefox has exited' not in exited.stderr:
                raise RuntimeError(f'exited process not rejected: {exited.stdout} {exited.stderr}')
            print('Firefox inspector rejects wrong profile and exited process; accepts expected profile')
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
