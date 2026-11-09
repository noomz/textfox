#!/usr/bin/env python3
"""Inspect one conditional extension toolbar icon in disposable Firefox profiles.

The temporary extension uses an existing selector's ID only inside the fixture.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ID = 'uBlock0@raymondhill.net'
PREFERENCE = 'shyfox.enable.ext.mono.toolbar.icons'


def check(nova, enabled):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-extension-icon-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        addon = profile / 'textfox-fixture.xpi'
        manifest = {'manifest_version':2, 'name':'Textfox selector fixture', 'version':'1.0',
                    'browser_specific_settings':{'gecko':{'id':EXTENSION_ID}},
                    'browser_action':{'default_title':'Textfox fixture', 'default_icon':'fixture.svg'}}
        with zipfile.ZipFile(addon, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('fixture.svg', '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="black" d="M0 0h16v16H0z"/></svg>')
        prefs = {'marionette.port':port, 'browser.nova.enabled':nova, PREFERENCE:enabled,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
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
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref(arguments[0])};
                """, script_args=[PREFERENCE], sandbox='system')
            if (process.poll() is not None or Path(metadata['profile']).resolve() != profile.resolve()
                    or metadata['nova'] != nova or metadata['iconPref'] != enabled):
                raise RuntimeError('unexpected Firefox process, profile, or preferences')
            installed = m.execute_async_script("""
                const done=arguments[arguments.length-1];
                const file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                file.initWithPath(arguments[0]);
                AddonManager.installTemporaryAddon(file).then(a=>done({id:a.id,active:a.isActive}),e=>done({error:String(e)}));
                """, script_args=[str(addon)], sandbox='system')
            if installed.get('id') != EXTENSION_ID or not installed.get('active'):
                raise RuntimeError(f'temporary extension did not install: {installed}')
            widget_id = m.execute_script("""
                const {ExtensionCommon}=ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');
                return ExtensionCommon.makeWidgetId(arguments[0])+'-browser-action';
                """, script_args=[EXTENSION_ID], sandbox='system')
            m.execute_script('CustomizableUI.addWidgetToArea(arguments[0], CustomizableUI.AREA_NAVBAR)',
                             script_args=[widget_id], sandbox='system')
            deadline = time.monotonic() + 20
            while True:
                sample = m.execute_script("""
                    const button=document.querySelector('#nav-bar .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"]');
                    const icon=button?.querySelector('.toolbarbutton-icon');
                    if (!icon) return null;
                    const s=getComputedStyle(icon),r=icon.getBoundingClientRect();
                    return {id:button.id, widget:button.closest('toolbaritem')?.id, image:s.listStyleImage, display:s.display,
                      visibility:s.visibility,width:r.width,height:r.height,
                      legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.ext.mono.toolbar.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.ext.mono.toolbar.icons")').matches};
                    """, sandbox='system')
                if sample and sample['width'] > 0 and sample['height'] > 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'extension toolbar fixture unavailable: {sample}')
                time.sleep(.1)
            if sample['modern'] != enabled or sample['widget'] != widget_id:
                raise RuntimeError(f'extension selector or preference control failed: {sample}, expected {widget_id}')
            target = (profile / 'chrome/icons/ublock.svg').as_uri()
            script = """
                const priority=arguments[1] ? ' !important' : '';
                const css=`@media -moz-pref("shyfox.enable.ext.mono.toolbar.icons") { .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"] .toolbarbutton-icon { list-style-image: url("${arguments[0]}")${priority}; } }`;
                const uri='data:text/css,'+encodeURIComponent(css);
                window.windowUtils.loadSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET);
                try { return getComputedStyle(document.querySelector('#nav-bar .webextension-browser-action[data-extensionid="uBlock0@raymondhill.net"] .toolbarbutton-icon')).listStyleImage; }
                finally { window.windowUtils.removeSheetUsingURIString(uri, Ci.nsIDOMWindowUtils.USER_SHEET); }
                """
            control = {priority:m.execute_script(script, script_args=[target, important], sandbox='system')
                       for priority, important in (('normal', False), ('important', True))}
            if any((target in value) != enabled for value in control.values()):
                raise RuntimeError(f'extension icon intervention inconclusive: {sample}, {control}')
            print(json.dumps({'firefox':metadata,'extension':installed,'sample':sample,'control':control}), flush=True)
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
        for enabled in (False, True):
            check(nova, enabled)
