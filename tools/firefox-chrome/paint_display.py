"""Headed Firefox paint capture for the painted-pixel tests, per platform.

A display starts (or uses) a desktop Firefox can paint on, launches and ends
Firefox there, and captures what the OS composited for the Firefox window:

- Linux: a disposable headless Sway compositor, captured with grim (see
  test-nova-layout-live.py). The capture is the whole 1280x800 output.
  Firefox is a Wayland client, or with TF_PAINT_X11=1 an X11 client of the
  compositor's Xwayland (asserted from Firefox's own window protocol).
- macOS: the logged-in desktop, captured with `screencapture -R` over the
  Firefox window. The capturing process needs Screen Recording permission.
- Windows: the interactive desktop, captured with GDI BitBlt (CAPTUREBLT, so
  layered popups are included) over the Firefox window. Run inside the desktop
  session (a scheduled task); a service or SSH session has no desktop.

FIREFOX names the Firefox to launch. Outside Windows it is split like a shell
command line, so it may be a launcher with arguments (a nix-portable store path).

Captures are binary PPM files. `diff` compares two captures in pure Python, so
macOS and Windows need no ImageMagick.
"""
import os
from pathlib import Path
import runpy
import shlex
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent

# Window rect in CSS pixels, its screen, and the device pixel ratio, read in chrome.
WINDOW_JS = ("return {x:window.screenX, y:window.screenY, width:window.outerWidth, height:window.outerHeight, "
             "screenWidth:screen.width, screenHeight:screen.height, dpr:window.devicePixelRatio}")


def read_ppm(path):
    data = Path(path).read_bytes()
    fields, position = [], 0
    while len(fields) < 4:  # magic, width, height, maxval; these writers put no comments
        while data[position:position + 1].isspace():
            position += 1
        end = position
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[position:end])
        position = end
    if fields[0] != b'P6' or fields[3] != b'255':
        raise RuntimeError(f'unexpected capture header {fields} in {path}')
    return int(fields[1]), int(fields[2]), data[position + 1:]


def write_ppm(path, width, height, rgb):
    Path(path).write_bytes(b'P6\n%d %d\n255\n' % (width, height) + bytes(rgb))


def rgb_from_bgra(buffer, width, height, stride, top_down):
    """Packed RGB rows, top row first, from 32-bit BGRA rows."""
    out = bytearray(width * height * 3)
    for row in range(height):
        start = (row if top_down else height - 1 - row) * stride
        line = buffer[start:start + width * 4]
        target = bytearray(width * 3)
        target[0::3], target[1::3], target[2::3] = line[2::4], line[1::4], line[0::4]
        out[row * width * 3:(row + 1) * width * 3] = target
    return out


def read_bmp(path):
    """(width, height, RGB) from a 24- or 32-bit uncompressed or bitfield BMP."""
    data = Path(path).read_bytes()
    if data[:2] != b'BM':
        raise RuntimeError(f'{path} is not a BMP')
    offset = int.from_bytes(data[10:14], 'little')
    width = int.from_bytes(data[18:22], 'little', signed=True)
    height = int.from_bytes(data[22:26], 'little', signed=True)
    bits = int.from_bytes(data[28:30], 'little')
    compression = int.from_bytes(data[30:34], 'little')
    top_down, height = height < 0, abs(height)
    if bits == 32 and compression in (0, 3):  # 3: BI_BITFIELDS; screencapture writes BGRA masks
        if compression == 3 and int.from_bytes(data[54:58], 'little') != 0x00ff0000:
            raise RuntimeError(f'{path}: unexpected BMP channel masks')
        return width, height, rgb_from_bgra(data[offset:], width, height, width * 4, top_down)
    if bits == 24 and compression == 0:
        stride = (width * 3 + 3) & ~3
        out = bytearray(width * height * 3)
        for row in range(height):
            start = offset + (row if top_down else height - 1 - row) * stride
            line = data[start:start + width * 3]
            target = bytearray(width * 3)
            target[0::3], target[1::3], target[2::3] = line[2::3], line[1::3], line[0::3]
            out[row * width * 3:(row + 1) * width * 3] = target
        return width, height, out
    raise RuntimeError(f'{path}: unsupported BMP ({bits} bits, compression {compression})')


def diff(first, second, out=None, noise=0):
    """Changed-pixel count and bounding box between two captures; optional PGM mask at out.

    A pixel counts as changed when some channel differs by more than noise (0: any difference)."""
    width, height, a = read_ppm(first)
    other = read_ppm(second)
    if other[:2] != (width, height):
        raise RuntimeError(f'capture sizes differ: {(width, height)} and {other[:2]}')
    b = other[2]
    row = width * 3
    mask = bytearray(width * height) if out else None
    total, left, top, right, bottom = 0, width, height, -1, -1
    for y in range(height):
        start = y * row
        if a[start:start + row] == b[start:start + row]:
            continue
        for x in range(width):
            i = start + x * 3
            if a[i:i + 3] != b[i:i + 3] and (not noise or max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]),
                                                                abs(a[i + 2] - b[i + 2])) > noise):
                total += 1
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
                if mask is not None:
                    mask[y * width + x] = 255
    if out:
        Path(out).write_bytes(b'P5\n%d %d\n255\n' % (width, height) + bytes(mask))
    box = None if not total else {'x': left, 'y': top, 'width': right - left + 1, 'height': bottom - top + 1}
    return total, box


class Display:
    """A desktop to run headed Firefox on. Subclasses capture and end processes per platform."""
    platform = None
    expected_os = None

    def __init__(self, directory, env):
        self.directory, self.env = Path(directory), env
        command = os.environ.get('FIREFOX', 'firefox')
        # A command list; Windows paths keep their backslashes as one word.
        self.firefox = [command] if sys.platform == 'win32' else shlex.split(command)
        self.rect = None  # device-pixel capture rect, set by bind()
        self.dpr = 1

    @property
    def firefox_env(self):
        """Environment Firefox launches with; captures use self.env."""
        return self.env

    @property
    def size(self):
        return (self.rect['width'], self.rect['height'])

    def check(self, meta):
        """Problems with the Firefox Marionette reached, beyond the caller's own checks."""
        problems = []
        if meta.get('os') != self.expected_os:
            problems.append(f'OS {meta.get("os")}, not {self.expected_os}')
        return problems

    def bind(self, m):
        """Maximize the Firefox window and capture its rect from now on."""
        m.execute_script('window.maximize()', sandbox='system')
        time.sleep(2)  # the window manager animates the zoom
        w = m.execute_script(WINDOW_JS, sandbox='system')
        # Clamp to the screen: a maximized window's invisible resize borders lie outside it.
        left, top = max(0, w['x']), max(0, w['y'])
        right = min(w['screenWidth'], w['x'] + w['width'])
        bottom = min(w['screenHeight'], w['y'] + w['height'])
        self.dpr = w['dpr']
        self.points = {'x': left, 'y': top, 'width': right - left, 'height': bottom - top}
        self.rect = {k: round(v * self.dpr) for k, v in self.points.items()}
        return dict(w, captured=self.rect)

    def stop_firefox(self, process):
        """End Firefox and everything in its process group.

        Firefox starts in its own session (popen_options), so a launcher such as
        nix-portable's bwrap cannot leave it running and writing the profile."""
        try:
            group = os.getpgid(process.pid)
        except ProcessLookupError:
            group = None
        if group is None or group != process.pid:  # not our own session: the launched process only
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return
        # macOS answers EPERM, not ESRCH, once only exiting or zombie members remain (B30): none
        # of them can still write the profile, so both mean the group is done.
        for sig, timeout in ((signal.SIGTERM, 15), (signal.SIGKILL, 10)):
            try:
                os.killpg(group, sig)
            except (ProcessLookupError, PermissionError):
                break
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                process.poll()
                try:
                    os.killpg(group, 0)
                except (ProcessLookupError, PermissionError):
                    break  # group empty
                time.sleep(.2)
            else:
                continue
            break
        process.wait()

    @staticmethod
    def popen_options():
        """Popen keyword arguments for launching Firefox."""
        return {} if sys.platform == 'win32' else {'start_new_session': True}

    def close(self):
        pass


class SwayDisplay(Display):
    expected_os = 'Linux'

    def __init__(self, directory, env):
        super().__init__(directory, env)
        self.x11 = os.environ.get('TF_PAINT_X11') == '1'
        self.platform = 'linux-x11' if self.x11 else 'linux-wayland'
        layout = runpy.run_path(str(HERE / 'test-nova-layout-live.py'))
        self._stop, self.output = layout['stop'], layout['OUTPUT']
        self.sway = layout['start_sway'](self.directory, env, xwayland=self.x11)

    @property
    def firefox_env(self):
        if not self.x11:
            return self.env
        # No Wayland socket: GTK and Firefox can only reach the compositor through Xwayland.
        env = {k: v for k, v in self.env.items() if k != 'WAYLAND_DISPLAY'}
        return dict(env, MOZ_ENABLE_WAYLAND='0', GDK_BACKEND='x11')

    def check(self, meta):
        problems = super().check(meta)
        if meta.get('dpr') != 1:
            problems.append('not at scale 1')
        if self.x11:
            if meta.get('windowProtocol') != 'x11' or meta.get('wayland'):
                problems.append(f'not an X11 client: protocol {meta.get("windowProtocol")}, '
                                f'WAYLAND_DISPLAY {meta.get("wayland")}')
        elif not meta.get('wayland') or meta.get('windowProtocol') not in (None, 'wayland'):
            problems.append('not on the Sway Wayland display')
        return problems

    def bind(self, m):
        # Sway tiles the only window over the whole output; the capture is the output.
        self.rect = {'x': 0, 'y': 0, 'width': self.output[0], 'height': self.output[1]}
        return {'captured': self.rect}

    def capture(self, path):
        for attempt in range(3):  # grim fails now and then while a window maps or unmaps
            if subprocess.run(['grim', '-t', 'ppm', str(path)], env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                return path
            time.sleep(.5)
        raise RuntimeError(f'grim could not capture {path}')

    def close(self):
        self._stop(self.sway, 10)


class MacDisplay(Display):
    platform = 'macos'
    expected_os = 'Darwin'

    def bind(self, m):
        # Started from a background process, Firefox opens behind the frontmost app;
        # a covered window captures someone else's pixels.
        m.execute_script("Cc['@mozilla.org/widget/macdocksupport;1'].getService(Ci.nsIMacDockSupport)"
                         ".activateApplication(true); window.focus()", sandbox='system')
        deadline = time.monotonic() + 10
        while not m.execute_script('return document.hasFocus()', sandbox='system'):
            if time.monotonic() >= deadline:
                raise RuntimeError('Firefox did not become the frontmost app')
            time.sleep(.2)
        return super().bind(m)

    def capture(self, path):
        bmp = self.directory / 'capture.bmp'
        region = ','.join(str(round(self.points[k])) for k in ('x', 'y', 'width', 'height'))
        result = subprocess.run(['screencapture', '-x', '-t', 'bmp', '-R', region, str(bmp)],
                                capture_output=True, text=True)
        if result.returncode or not bmp.exists():
            raise RuntimeError(f'screencapture failed ({result.returncode}): {result.stderr.strip()} '
                               '(the capturing process needs Screen Recording permission)')
        width, height, rgb = read_bmp(bmp)
        if (width, height) != self.size:
            raise RuntimeError(f'screencapture size {(width, height)}, expected {self.size}')
        write_ppm(path, width, height, rgb)
        return path


class WindowsDisplay(Display):
    platform = 'windows'
    expected_os = 'WINNT'

    def __init__(self, directory, env):
        super().__init__(directory, env)
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        try:  # device pixels, matching Firefox's devicePixelRatio scaling
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except OSError:
            ctypes.windll.user32.SetProcessDPIAware()
        handle = ctypes.c_void_p
        self.user32, self.gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        self.user32.GetDC.restype = handle
        self.user32.GetDC.argtypes = [handle]
        self.user32.ReleaseDC.argtypes = [handle, handle]
        self.gdi32.CreateCompatibleDC.restype = handle
        self.gdi32.CreateCompatibleDC.argtypes = [handle]
        self.gdi32.CreateCompatibleBitmap.restype = handle
        self.gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
        self.gdi32.SelectObject.restype = handle
        self.gdi32.SelectObject.argtypes = [handle, handle]
        self.gdi32.BitBlt.argtypes = [handle] + [ctypes.c_int] * 4 + [handle, ctypes.c_int, ctypes.c_int,
                                                                      wintypes.DWORD]
        self.gdi32.GetDIBits.argtypes = [handle, handle, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.UINT]
        self.gdi32.DeleteObject.argtypes = [handle]
        self.gdi32.DeleteDC.argtypes = [handle]

    def capture(self, path):
        ctypes, wintypes = self.ctypes, self.wintypes

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                        ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD),
                        ('biCompression', wintypes.DWORD), ('biSizeImage', wintypes.DWORD),
                        ('biXPelsPerMeter', wintypes.LONG), ('biYPelsPerMeter', wintypes.LONG),
                        ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]

        x, y, width, height = (self.rect[k] for k in ('x', 'y', 'width', 'height'))
        screen = self.user32.GetDC(None)
        memory = self.gdi32.CreateCompatibleDC(screen)
        bitmap = self.gdi32.CreateCompatibleBitmap(screen, width, height)
        previous = self.gdi32.SelectObject(memory, bitmap)
        try:
            srccopy, captureblt = 0x00CC0020, 0x40000000
            if not self.gdi32.BitBlt(memory, 0, 0, width, height, screen, x, y, srccopy | captureblt):
                raise ctypes.WinError()
            header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            buffer = ctypes.create_string_buffer(width * height * 4)
            if self.gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0) != height:
                raise ctypes.WinError()
        finally:
            self.gdi32.SelectObject(memory, previous)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory)
            self.user32.ReleaseDC(None, screen)
        write_ppm(path, width, height, rgb_from_bgra(buffer.raw, width, height, width * 4, True))
        return path

    def stop_firefox(self, process):
        # terminate() stops only the parent; child processes would keep the profile open.
        # Windows has no process groups (os.getpgid); the tree kill above is the group stop.
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def open_display(directory, env):
    if sys.platform == 'darwin':
        return MacDisplay(directory, env)
    if sys.platform == 'win32':
        return WindowsDisplay(directory, env)
    return SwayDisplay(directory, env)
