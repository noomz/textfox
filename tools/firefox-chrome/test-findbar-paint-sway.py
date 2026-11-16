#!/usr/bin/env python3
"""Compare findbar focus pixels under a disposable headless Sway compositor.

Requires sway, grim, ImageMagick, and Firefox/Marionette. This checks a small
pixel region, not contrast or accessibility on a real desktop.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent


def changed(first, second, crop):
    out = subprocess.check_output(['magick', str(first), str(second), '-crop', crop, '+repage',
                                   '-compose', 'difference', '-composite', '-colorspace', 'gray',
                                   '-threshold', '0', '-format', '%[fx:mean*w*h]', 'info:'], text=True)
    return round(float(out))


def main():
    with tempfile.TemporaryDirectory(prefix='tf-findbar-sway-') as directory:
        root = Path(directory)
        root.chmod(0o700)
        config = root / 'config'
        config.write_text('output * mode 1280x800\nseat seat0 fallback true\n')
        env = os.environ.copy()
        env.update({'XDG_RUNTIME_DIR':directory, 'WLR_BACKENDS':'headless', 'WLR_RENDERER':'pixman',
                    'WLR_LIBINPUT_NO_DEVICES':'1', 'MOZ_ENABLE_WAYLAND':'1',
                    'TEXTFOX_FINDBAR_PAINT_DIR':directory,
                    'PYTHONPATH':str(HERE) + os.pathsep + env.get('PYTHONPATH', '')})
        env.pop('MOZ_HEADLESS', None)
        sway = subprocess.Popen(['sway', '--unsupported-gpu', '-c', str(config)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            while True:
                sockets = [p for p in root.glob('wayland-*') if p.is_socket()]
                if sockets:
                    env['WAYLAND_DISPLAY'] = sockets[0].name
                    break
                if sway.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('headless Sway did not start')
                time.sleep(.1)
            for nova in (False, True):
                for themed in (False, True):
                    code = ("import runpy; runpy.run_path(" + repr(str(HERE / 'test-findbar-live.py')) + ")"
                            "['check'](" + repr(nova) + ',' + repr(themed) + ',False,False)')
                    result = subprocess.run([sys.executable, '-c', code], env=env, text=True,
                                            capture_output=True, check=True, timeout=120)
                    records = [json.loads(line) for line in result.stdout.splitlines()]
                    case = next(row['keyboardCase'] for row in records if 'keyboardCase' in row)
                    focus, files = case['focus'], case['paintFiles']
                    if focus['devicePixelRatio'] != 1:
                        raise RuntimeError(f'unsupported compositor scale: {focus}')
                    width, height = map(int, subprocess.check_output(
                        ['magick', 'identify', '-format', '%w %h', files['focused']], text=True).split())
                    x, y = int(focus['checkScreenX']) - 5, int(focus['checkScreenY']) - 5
                    w, h = int(focus['checkWidth']) + 10, int(focus['checkHeight']) + 10
                    if x < 0 or y < 0 or x + w > width or y + h > height:
                        raise RuntimeError(f'focus crop outside screenshot: {focus}, {(width,height)}')
                    crop = f'{w}x{h}+{x}+{y}'
                    diffs = {name:changed(files['focused'], files[name], crop)
                             for name in ('focused-control', 'blurred', 'restored')}
                    if diffs['focused-control'] or not diffs['blurred'] or diffs['restored']:
                        raise RuntimeError(f'focus pixel controls inconclusive: {diffs}, {crop}, {focus}')
                    print(json.dumps({'nova':nova, 'themed':themed, 'crop':crop,
                                      'screenshotSize':[width,height], 'changedPixels':diffs}), flush=True)
        finally:
            sway.terminate()
            try:
                sway.wait(timeout=10)
            except subprocess.TimeoutExpired:
                sway.kill()
                sway.wait()


if __name__ == '__main__':
    main()
