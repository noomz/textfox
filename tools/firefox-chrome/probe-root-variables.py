#!/usr/bin/env python3
"""Compare shared root variables and their inputs in disposable Firefox profiles."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]


def check(nova, themed):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-root-vars-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
            (profile / 'chrome' / 'config.css').write_text(
                ':root { --tf-border-width: 7px; --tf-rounding: 3px; '
                '--tf-bg: rgb(23, 47, 71); --tf-accent: rgb(19, 83, 137); '
                '--tf-display-nav-buttons: flex; }\n')
        prefs = {'marionette.port': port, 'browser.nova.enabled': nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets': True,
                 'browser.shell.checkDefaultBrowser': False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                    '--new-instance', '--profile', str(profile), 'about:blank'],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            metadata = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve() or metadata['nova'] != nova:
                raise RuntimeError('unexpected Firefox process, profile, or Nova preference')
            m.set_context('content')
            m.navigate('data:text/html,toolbar-hover-first')
            m.navigate('data:text/html,toolbar-hover-second')
            m.set_context('chrome')
            deadline = time.monotonic() + 15
            while not m.execute_script("return !!document.querySelector('#back-button:not([disabled]) .toolbarbutton-icon')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('enabled Back button did not initialize')
                time.sleep(.1)
            result = m.execute_script("""
                const root=document.documentElement;
                const names=['--toolbar-color','--link-color','--urlbarView-highlight-background',
                  '--toolbox-non-lwt-bgcolor','--toolbar-field-background-color','--border-width',
                  '--border-radius-small','--border-radius-medium','--toolbarbutton-border-radius',
                  '--tab-border-radius','--tab-selected-outline-color','--tab-selected-bgcolor',
                  '--tab-hover-background-color','--tab-min-height','--toolbarbutton-hover-background',
                  '--button-background-color-active','--inactive-titlebar-opacity','--toolbar-field-color',
                  '--toolbar-bgcolor'];
                const measure=()=>{const s=getComputedStyle(root); return Object.fromEntries(names.map(n=>[n,s.getPropertyValue(n).trim()]));};
                const before=measure();
                if (!arguments[0]) return {before,intervention:null,restored:null};
                const inputs=['--toolbar-field-color','--toolbar-bgcolor'];
                const saved=inputs.map(n=>({name:n,value:root.style.getPropertyValue(n),priority:root.style.getPropertyPriority(n)}));
                let intervention;
                try {
                  root.style.setProperty(inputs[0],'rgb(240, 240, 240)','important');
                  root.style.setProperty(inputs[1],'rgb(23, 47, 71)','important');
                  intervention=measure();
                } finally {
                  for (const {name,value,priority} of saved) {
                    if (value) root.style.setProperty(name,value,priority);
                    else root.style.removeProperty(name);
                  }
                }
                return {before,intervention,restored:measure()};
                """, script_args=[themed], sandbox='system')
            if themed:
                before, control, restored = result['before'], result['intervention'], result['restored']
                if (before['--border-width'] != '7px' or before['--tab-border-radius'] != '3px'
                        or before['--toolbar-field-background-color'] != 'rgb(23, 47, 71)'):
                    raise RuntimeError(f'configured root values missing: {before}')
                outputs = ('--tab-selected-bgcolor', '--tab-hover-background-color',
                           '--toolbarbutton-hover-background', '--button-background-color-active')
                if any(not control[name] for name in outputs) or restored != before:
                    raise RuntimeError(f'root input intervention inconclusive: {result}')
            hover = m.execute_script("""
                const button=document.getElementById('back-button'), icon=button.querySelector('.toolbarbutton-icon');
                const inputs=['--toolbarbutton-hover-background','--toolbarbutton-background-color-hover','transition'];
                const saved=inputs.map(n=>({name:n,value:icon.style.getPropertyValue(n),priority:icon.style.getPropertyPriority(n)}));
                InspectorUtils.addPseudoClassLock(button, ':hover');
                try {
                  icon.style.setProperty('transition','none','important');
                  const measure=()=>({hover:button.matches(':hover'),background:getComputedStyle(icon).backgroundColor});
                  const before=measure();
                  icon.style.setProperty(inputs[0],'rgb(19, 83, 137)','important');
                  const oldToken=measure();
                  icon.style.removeProperty(inputs[0]);
                  icon.style.setProperty(inputs[1],'rgb(173, 89, 41)','important');
                  const currentToken=measure();
                  return {before,oldToken,currentToken};
                } finally {
                  InspectorUtils.removePseudoClassLock(button, ':hover');
                  for (const {name,value,priority} of saved) {
                    if (value) icon.style.setProperty(name,value,priority);
                    else icon.style.removeProperty(name);
                  }
                }
                """, sandbox='system')
            if not all(hover[key]['hover'] for key in ('before', 'oldToken', 'currentToken')):
                raise RuntimeError(f'Back button hover fixture failed: {hover}')
            if hover['currentToken']['background'] != 'rgb(173, 89, 41)':
                raise RuntimeError(f'current toolbar hover token control failed: {hover}')
            m.execute_script('PanelUI.show()', sandbox='system')
            deadline = time.monotonic() + 15
            while not m.execute_script("return document.getElementById('appMenu-popup').state === 'open' && !!document.querySelector('#appMenu-fullscreen-button2:not([disabled]) .toolbarbutton-icon')", sandbox='system'):
                if time.monotonic() >= deadline:
                    raise RuntimeError('app-menu fullscreen button did not initialize')
                time.sleep(.1)
            active = m.execute_script("""
                const button=document.getElementById('appMenu-fullscreen-button2'), icon=button.querySelector('.toolbarbutton-icon');
                const names=['--button-background-color-active','transition'];
                const saved=names.map(n=>({name:n,value:icon.style.getPropertyValue(n),priority:icon.style.getPropertyPriority(n)}));
                InspectorUtils.addPseudoClassLock(button, ':hover');
                InspectorUtils.addPseudoClassLock(button, ':active');
                try {
                  icon.style.setProperty('transition','none','important');
                  const measure=()=>{const s=getComputedStyle(icon),r=icon.getBoundingClientRect();
                    return {hover:button.matches(':hover'),active:button.matches(':active'),background:s.backgroundColor,
                      token:s.getPropertyValue('--button-background-color-active').trim(),width:r.width,height:r.height};};
                  const before=measure();
                  icon.style.setProperty(names[0],'rgb(173, 89, 41)','important');
                  return {before,control:measure()};
                } finally {
                  InspectorUtils.removePseudoClassLock(button, ':active');
                  InspectorUtils.removePseudoClassLock(button, ':hover');
                  for (const {name,value,priority} of saved) {
                    if (value) icon.style.setProperty(name,value,priority);
                    else icon.style.removeProperty(name);
                  }
                }
                """, sandbox='system')
            if (not active['before']['hover'] or not active['before']['active']
                    or active['before']['width'] <= 0 or active['before']['height'] <= 0
                    or active['control']['background'] != 'rgb(173, 89, 41)'):
                raise RuntimeError(f'app-menu active icon fixture failed: {active}')
            print(json.dumps({'firefox':metadata, 'themed':themed, 'result':result,
                              'backHover':hover, 'appMenuActive':active}), flush=True)
        finally:
            try:
                if m:
                    m.delete_session()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    for nova in (False, True):
        for themed in (False, True):
            check(nova, themed)
