#!/usr/bin/env python3
"""Read Firefox chrome state from Marionette inside a disposable VM."""
import json
from pathlib import Path
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
    deadline = time.monotonic() + 110
    while True:
        try:
            sock = socket.create_connection(('127.0.0.1', 2828), timeout=3)
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
                const names = ['browser.nova.enabled', 'browser.newtabpage.activity-stream.nova.enabled'];
                return {
                  profile: Services.dirsvc.get('ProfD', Ci.nsIFile).path,
                  version: Services.appinfo.version,
                  build: Services.appinfo.appBuildID,
                  navbarBorder: getComputedStyle(document.getElementById('nav-bar')).borderTopWidth,
                  preferences: Object.fromEntries(names.map(n => [n, {
                    effective: p.getBoolPref(n), default: defaults.getBoolPref(n), user: p.prefHasUserValue(n)
                  }]))
                };
            ''',
            'args': [], 'sandbox': 'system', 'newSandbox': True,
        })['value']
    Path('/tmp/shared/preferences.json').write_text(json.dumps(result))
    if result['profile'] != '/home/probe/.config/mozilla/firefox/test':
        raise RuntimeError('unexpected Firefox profile')
    if result['navbarBorder'] != '7px' or result['preferences']['browser.nova.enabled']['effective']:
        raise RuntimeError('Home Manager theme or Nova preference not applied')


if __name__ == '__main__':
    main()
