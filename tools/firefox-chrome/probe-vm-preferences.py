#!/usr/bin/env python3
"""Read Firefox chrome state from Marionette inside a disposable VM."""
import argparse
import json
from pathlib import Path
import re
import socket
import time


def receive(sock):
    size = b''
    while not size.endswith(b':'):
        data = sock.recv(1)
        if not data:
            raise RuntimeError('Marionette closed before message length')
        size += data
    remaining = int(size[:-1])
    data = b''
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError('Marionette closed before message body')
        data += chunk
        remaining -= len(chunk)
    return json.loads(data)


def command(sock, number, name, params):
    payload = json.dumps([0, number, name, params]).encode()
    sock.sendall(str(len(payload)).encode() + b':' + payload)
    result = receive(sock)
    if result[2] is not None:
        raise RuntimeError(str(result))
    return result[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--border')
    parser.add_argument('--port', type=int, default=2828)
    parser.add_argument('--output', default='/tmp/shared/preferences.json')
    # default: no user value, Firefox's own default applies (T25: textfox no longer
    # assigns browser.nova.enabled and clears the false it persisted earlier).
    parser.add_argument('--expect-nova', choices=('on', 'off', 'default'), default='default')
    parser.add_argument('--defaults-css', default='/tmp/shared/defaults.css')
    parser.add_argument('--expect-vars', default='{}')
    parser.add_argument('--expect-prefs', default='{}')
    # A vanilla Firefox is the negative control: no --tf-* variable may be defined.
    parser.add_argument('--no-textfox-css', action='store_true')
    # A used profile's own preferences and files that delivery must leave alone.
    parser.add_argument('--preserve-pref', action='append', default=[], metavar='NAME=JSONVALUE')
    parser.add_argument('--preserve-file', action='append', default=[], metavar='PATH=TEXT')
    args = parser.parse_args()
    expect_vars = json.loads(args.expect_vars)
    expect_prefs = json.loads(args.expect_prefs)
    preserve_prefs = {}
    for item in args.preserve_pref:
        name, _, value = item.partition('=')
        preserve_prefs[name] = json.loads(value)
    preserve_files = dict(item.partition('=')[::2] for item in args.preserve_file)
    variables = re.findall(r'(--tf-[\w-]+)\s*:', Path(args.defaults_css).read_text())
    names = ['browser.nova.enabled', 'browser.newtabpage.activity-stream.nova.enabled']
    names += sorted(set(expect_prefs) | set(preserve_prefs))
    deadline = time.monotonic() + 110
    while True:
        try:
            sock = socket.create_connection(('127.0.0.1', args.port), timeout=3)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    with sock:
        sock.settimeout(30)
        receive(sock)
        command(sock, 1, 'WebDriver:NewSession', {'capabilities': {'alwaysMatch': {}}})
        command(sock, 2, 'Marionette:SetContext', {'value': 'chrome'})
        result = command(sock, 3, 'WebDriver:ExecuteScript', {
            'script': '''
                const p = Services.prefs, defaults = p.getDefaultBranch('');
                const readBool = (branch, name) => {
                  try { return branch.getBoolPref(name); } catch (_) { return null; }
                };
                const names = arguments[0], vars = arguments[1];
                const style = getComputedStyle(document.documentElement);
                return {
                  profile: Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version: Services.appinfo.version,
                  build: Services.appinfo.appBuildID,
                  navbarBorder: getComputedStyle(document.getElementById('nav-bar')).borderTopWidth,
                  variables: Object.fromEntries(vars.map(n => [n, style.getPropertyValue(n).trim()])),
                  preferences: Object.fromEntries(names.map(n => [n, {
                    effective: readBool(p, n), default: readBool(defaults, n), user: p.prefHasUserValue(n)
                  }]))
                };
            ''',
            'args': [names, sorted(set(variables) | set(expect_vars))],
            'sandbox': 'system', 'newSandbox': True,
        })['value']
    Path(args.output).write_text(json.dumps(result))
    if result['profile'] != args.profile:
        raise RuntimeError('unexpected Firefox profile')
    if args.border is not None and result['navbarBorder'] != args.border:
        raise RuntimeError('configured navbar border not applied')
    nova = result['preferences']['browser.nova.enabled']
    if args.expect_nova == 'default':
        if nova['user'] or nova['effective'] != nova['default']:
            raise RuntimeError(f'browser Nova is not at Firefox\'s default: {nova}')
    elif nova['effective'] != (args.expect_nova == 'on'):
        raise RuntimeError('browser Nova preference did not match expectation')
    # Off must come from a user value textfox set, or from a build whose own
    # default is already off; anything else means the probe read the wrong pref.
    if args.expect_nova == 'off' and not (nova['user'] or nova['default'] is False):
        raise RuntimeError(f'browser Nova is off without a user value or an off default: {nova}')
    if not variables:
        raise RuntimeError(f'no variables read from {args.defaults_css}')
    if args.no_textfox_css:
        defined = [name for name in variables if result['variables'][name]]
        if defined:
            raise RuntimeError(f'Firefox without textfox defines {defined}')
    else:
        empty = [name for name in variables if not result['variables'][name]]
        if empty:
            raise RuntimeError(f'generated CSS does not define {empty}')
    for name, value in expect_vars.items():
        if result['variables'][name] != value:
            raise RuntimeError(f'{name} is {result["variables"][name]!r}, expected {value!r}')
    for name, value in expect_prefs.items():
        observed = result['preferences'][name]['effective']
        if observed != value:
            raise RuntimeError(f'preference {name} is {observed!r}, expected {value!r}')
    for name, value in preserve_prefs.items():
        observed = result['preferences'][name]['effective']
        if observed != value:
            raise RuntimeError(f'preserved preference {name} is {observed!r}, expected {value!r}')
    for path, text in preserve_files.items():
        observed = Path(path).read_text() if Path(path).is_file() else None
        if observed != text:
            raise RuntimeError(f'preserved file {path} holds {observed!r}, expected {text!r}')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        Path('/tmp/shared/probe-error.txt').write_text(repr(error))
        raise
