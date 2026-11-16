#!/usr/bin/env python3
"""Clear the persisted `browser.nova.enabled` user value from a Firefox profile, once.

textfox keeps Nova off by assigning `browser.nova.enabled` false on every start.
Firefox stores that as a user value in `prefs.js`, and keeps it after the
assignment is gone, so retiring the assignment is not enough to give the profile
Firefox's default back (V3). `user.js` can set a preference but cannot clear one,
so the manual and home-manager paths clear it here, outside Firefox.

Run it only after the profile's `user.js` no longer assigns Nova. A `user.js`
that still does (an old textfox block, a pre-marker copy without markers, or the
user's own line) writes the value back on every start, and the marker below would
then stop every later clear, so an unmarked profile in that state is refused.

Editing `prefs.js` while Firefox runs is unsafe: Firefox rewrites the file from
memory and would put the value back. The script therefore takes Firefox's own
profile lock, an fcntl lock on `.parentlock`, for the whole edit. A Firefox that
holds the profile makes the script exit 75 without touching anything, and a
Firefox started during the edit refuses the locked profile.

The clear is one-shot. Only a `false` value is removed, since that is the only
value textfox ever wrote. The same atomic rewrite adds `textfox.nova.cleared`
true, and a profile carrying that marker is left alone, so a value the user sets
later is kept. The marker lives in `prefs.js`: resetting it in `about:config`,
Refresh Firefox, or deleting `prefs.js` removes it, and a later run would then
clear a `false` the user chose. A run that is refused or interrupted writes no
marker and can simply be repeated.

`--check-lock` only tests the lock: it touches nothing and skips the user.js
check, so an installer can refuse to start while Firefox holds the profile.

Exit status: 0 cleared or already cleared (with `--check-lock`: lock free), 75
Firefox holds the profile (close it and run again), 1 refused or failed.
"""
import argparse
import errno
import fcntl
import os
from pathlib import Path
import re
import sys
import tempfile
import time

PREF = 'browser.nova.enabled'
MARKER = 'textfox.nova.cleared'
MARKER_LINE = f'user_pref("{MARKER}", true);\n'.encode()
TEMP_PREFIX = 'prefs.js.textfox-'
EX_TEMPFAIL = 75
# Files Firefox writes into every profile it has opened.
PROFILE_FILES = ('prefs.js', 'times.json', 'compatibility.ini', 'user.js')

NOVA_FALSE = re.compile(rb'^\s*user_pref\(\s*"' + re.escape(PREF.encode()) + rb'"\s*,\s*false\s*\)\s*;')
NOVA_ANY = re.compile(r'^\s*user_pref\(\s*"' + re.escape(PREF) + r'"\s*,')
MARKER_TRUE = re.compile(rb'^\s*user_pref\(\s*"' + re.escape(MARKER.encode()) + rb'"\s*,\s*true\s*\)\s*;')


class Refused(Exception):
    pass


def lock_profile(profile):
    """Take Firefox's profile lock, or return None when Firefox holds it."""
    fd = os.open(profile / '.parentlock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise Refused(f'cannot take the profile lock on {profile}/.parentlock: {error}') from error
    return fd


def user_js_assigns_nova(profile):
    """Does user.js (through any link) assign Nova on any line?"""
    try:
        text = (profile / 'user.js').read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return False
    return any(NOVA_ANY.match(line) for line in text.split('\n'))


def already_marked(profile):
    """Read-only look at prefs.js for the marker; clear() checks again under the lock."""
    try:
        data, _ = read_prefs(profile / 'prefs.js')
    except Refused:
        return False
    return data is not None and any(MARKER_TRUE.match(line) for line in data.split(b'\n'))


def read_prefs(prefs):
    """Read prefs.js through one descriptor that refuses a symlink."""
    try:
        fd = os.open(prefs, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise Refused(f'{prefs} is a symlink; leaving it untouched') from error
        raise
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        return handle.read(), info


def backup_path(profile):
    stamp = time.strftime('%Y%m%d_%H%M%S')
    path = profile / f'prefs.js-{stamp}.textfox.bak'
    n = 0
    while path.exists():
        n += 1
        path = profile / f'prefs.js-{stamp}_{n}.textfox.bak'
    return path


def write_atomic(profile, target, data, info):
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=profile)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, info.st_mode & 0o777 if info else 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dir_fd = os.open(profile, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def clear(profile):
    prefs = profile / 'prefs.js'
    # Temp files of a run killed before its rename; the lock is held, so no run owns them.
    for leftover in profile.glob(TEMP_PREFIX + '*'):
        leftover.unlink()
    original, info = read_prefs(prefs)
    lines = original.split(b'\n') if original is not None else []
    if any(MARKER_TRUE.match(line) for line in lines):
        print(f'{PREF} already cleared in {prefs}')
        return 0
    if info is not None and info.st_uid != os.geteuid():
        raise Refused(f'{prefs} belongs to uid {info.st_uid}; run this as that user')

    kept = [line for line in lines if not NOVA_FALSE.match(line)]
    removed = len(lines) - len(kept)
    body = b'\n'.join(kept)
    if body and not body.endswith(b'\n'):
        body += b'\n'
    body += MARKER_LINE

    if original is not None:
        backup = backup_path(profile)
        write_atomic(profile, backup, original, info)
        print(f'Previous prefs.js kept at {backup}')
    write_atomic(profile, prefs, body, info)
    print(f'Removed {removed} {PREF} false line(s) from {prefs}; marked {MARKER}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('profile', type=Path, help='Firefox profile directory')
    parser.add_argument('--check-lock', action='store_true',
                        help='only test whether Firefox holds the profile; change nothing')
    args = parser.parse_args()
    profile = args.profile
    try:
        if not profile.is_dir() or not any((profile / name).exists() for name in PROFILE_FILES):
            raise Refused(f'{profile} is not a Firefox profile (none of {", ".join(PROFILE_FILES)})')
        if profile.stat().st_uid != os.geteuid():
            raise Refused(f'{profile} belongs to uid {profile.stat().st_uid}; run this as that user')
        if not args.check_lock and user_js_assigns_nova(profile) and not already_marked(profile):
            raise Refused(f'{profile}/user.js still assigns {PREF}; install the retired textfox '
                          'user.js (or remove that line) first')
        fd = lock_profile(profile)
        if fd is None:
            print(f'Firefox is using {profile}; close it and run this again', file=sys.stderr)
            return EX_TEMPFAIL
        try:
            return 0 if args.check_lock else clear(profile)
        finally:
            os.close(fd)
    except Refused as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
