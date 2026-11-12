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
    parser.add_argument('--expect-nova', choices=('on', 'off'), default='off')
    parser.add_argument('--defaults-css', default='/tmp/shared/defaults.css')
    parser.add_argument('--expect-vars', default='{}')
    parser.add_argument('--expect-prefs', default='{}')
    args = parser.parse_args()
    expect_vars = json.loads(args.expect_vars)
    expect_prefs = json.loads(args.expect_prefs)
    variables = re.findall(r'(--tf-[\w-]+)\s*:', Path(args.defaults_css).read_text())
    names = ['browser.nova.enabled', 'browser.newtabpage.activity-stream.nova.enabled']
    names += sorted(expect_prefs)
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
    if result['preferences']['browser.nova.enabled']['effective'] != (args.expect_nova == 'on'):
        raise RuntimeError('browser Nova preference did not match expectation')
    if not variables:
        raise RuntimeError(f'no variables read from {args.defaults_css}')
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


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        Path('/tmp/shared/probe-error.txt').write_text(repr(error))
        raise
