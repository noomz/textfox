#!/usr/bin/env python3
"""Check that concurrent live inspectors use separate disposable Firefox instances."""
import json
import os
from pathlib import Path
import subprocess

TOOL = Path(__file__).resolve().parent / 'firefox-live.sh'


def main():
    env = os.environ.copy()
    env['MOZ_HEADLESS'] = '1'
    env.pop('FF_MARIONETTE_PORT', None)
    processes = [subprocess.Popen(['sh', str(TOOL), '--nova', state, 'nav-bar'],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for state in ('on', 'off')]
    try:
        for state, process in zip(('on', 'off'), processes):
            stdout, stderr = process.communicate(timeout=90)
            if process.returncode != 0:
                raise RuntimeError(f'{state} inspector failed: {stderr}')
            result = json.loads(stdout)
            if result.get('id') != 'nav-bar' or result.get('novaEnabled') != (state == 'on'):
                raise RuntimeError(f'{state} inspector returned unexpected result: {result}')
        print('Concurrent Firefox inspectors returned their requested Nova states')
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
