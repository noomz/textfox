#!/usr/bin/env python3
"""Inspect supported extension toolbar selector identities using synthetic add-ons.

The fixtures contain no real extension code. All profiles and add-ons are temporary.
"""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

from ff_inspect import connect

ROOT=Path(__file__).resolve().parents[2]
PREF='shyfox.enable.ext.mono.toolbar.icons'
EXTENSIONS=(
    ('userchrome-toggle-extended@n2ezr.ru','command-frames.svg',False),
    ('uBlock0@raymondhill.net','ublock.svg',False),
    ('{446900e4-71c2-419f-a6a7-df9c091e268b}','bitwarden.svg',False),
    ('jid1-MnnxcxisBPnSXQ@jetpack','PrivacyBadger.svg',True),
    ('addon@darkreader.org','moon.svg',False),
    ('{b9db16a4-6edc-47ec-a1f4-b86292ed211d}','video-download-helper.svg',False),
    ('{c2c003ee-bd69-42a2-b0e9-6f34222cb046}','quit.svg',False),
    ('{8fbc7259-8015-4172-9af1-20e1edfbbd3a}','midnight-lizard.svg',True),
    ('{d7742d87-e61d-4b78-b8a1-b469842139fa}','vimium.svg',False),
    ('{7a7a4a92-a2a0-41d1-9fd7-1e92480d612d}','stylus.svg',False),
    ('@testpilot-containers','container.svg',False),
)


def check(nova, enabled):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-extension-matrix-') as directory:
        profile=Path(directory)
        shutil.copytree(ROOT / 'chrome',profile / 'chrome')
        addons=[]
        for index,(extension_id,icon,scaled) in enumerate(EXTENSIONS):
            archive_path=profile / f'fixture-{index}.xpi'
            manifest={'manifest_version':2,'name':f'Textfox toolbar fixture {index}','version':'1.0',
                      'browser_specific_settings':{'gecko':{'id':extension_id}},
                      'browser_action':{'default_title':f'Textfox fixture {index}','default_icon':'fixture.svg'}}
            with zipfile.ZipFile(archive_path,'w') as archive:
                archive.writestr('manifest.json',json.dumps(manifest))
                archive.writestr('fixture.svg','<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="black" d="M0 0h16v16H0z"/></svg>')
            addons.append({'id':extension_id,'archive':str(archive_path),
                           'icon':(profile / 'chrome/icons' / icon).as_uri(),'scaled':scaled})
        prefs={'marionette.port':port,'browser.nova.enabled':nova,PREF:enabled,
               'toolkit.legacyUserProfileCustomizations.stylesheets':True,
               'browser.shell.checkDefaultBrowser':False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k,v in prefs.items()))
        env=os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS']='1'
        process=subprocess.Popen(['firefox','--marionette','-remote-allow-system-access',
                                  '--new-instance','--profile',str(profile),'about:blank'],
                                 env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        m=None
        try:
            m=connect(port)
            m.set_window_rect(width=1600,height=900)
            m.set_context('chrome')
            meta=m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref('shyfox.enable.ext.mono.toolbar.icons')};
                """,sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve()!=profile.resolve()
                    or meta['nova']!=nova or meta['iconPref']!=enabled):
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            for addon in addons:
                installed=m.execute_async_script("""
                    const done=arguments[arguments.length-1];
                    const file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile); file.initWithPath(arguments[0]);
                    AddonManager.installTemporaryAddon(file).then(a=>done({id:a.id,active:a.isActive}),e=>done({error:String(e)}));
                    """,script_args=[addon['archive']],sandbox='system')
                if installed.get('id')!=addon['id'] or not installed.get('active'):
                    raise RuntimeError(f'fixture extension install failed: {addon["id"]}, {installed}')
                m.execute_script("""
                    const {ExtensionCommon}=ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');
                    CustomizableUI.addWidgetToArea(ExtensionCommon.makeWidgetId(arguments[0])+'-browser-action',CustomizableUI.AREA_NAVBAR);
                    """,script_args=[addon['id']],sandbox='system')
            deadline=time.monotonic()+20
            while True:
                sample=m.execute_script("""
                    const buttons=[...document.querySelectorAll('#nav-bar .webextension-browser-action')];
                    const items=arguments[0].map(a=>{
                      const e=buttons.find(b=>b.getAttribute('data-extensionid')===a.id), icon=e?.querySelector('.toolbarbutton-icon');
                      if(!icon)return {id:a.id,found:false};
                      const s=getComputedStyle(icon),r=icon.getBoundingClientRect();
                      return {id:a.id,found:true,image:s.listStyleImage,scale:s.scale,
                        display:s.display,visibility:s.visibility,width:r.width,height:r.height};
                    });
                    return {items,legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.ext.mono.toolbar.icons")').matches,
                      modern:matchMedia('-moz-pref("shyfox.enable.ext.mono.toolbar.icons")').matches};
                    """,script_args=[addons],sandbox='system')
                if all(i['found'] and i['width']>0 and i['height']>0 for i in sample['items']):
                    break
                if time.monotonic()>=deadline:
                    raise RuntimeError(f'extension toolbar fixtures unavailable: {sample}')
                time.sleep(.1)
            if sample['modern']!=enabled:
                raise RuntimeError(f'extension toolbar media control failed: {sample}')
            control=m.execute_script("""
                const entries=arguments[0];
                const rules=entries.map(a=>{
                  const scale=a.scaled?' scale: 1.3;':'';
                  return `:is(.webextension-browser-action,.eom-addon-button)[data-extensionid="${a.id}"] .toolbarbutton-icon { list-style-image: url("${a.icon}");${scale} }`;
                }).join(' ');
                const uri='data:text/css,'+encodeURIComponent(`@media -moz-pref("shyfox.enable.ext.mono.toolbar.icons") { ${rules} }`);
                window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                try {
                  const buttons=[...document.querySelectorAll('#nav-bar .webextension-browser-action')];
                  return entries.map(a=>{
                    const e=buttons.find(b=>b.getAttribute('data-extensionid')===a.id).querySelector('.toolbarbutton-icon');
                    const s=getComputedStyle(e);return {id:a.id,image:s.listStyleImage,scale:s.scale};
                  });
                } finally { window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET); }
                """,script_args=[addons],sandbox='system')
            for addon,value in zip(addons,control,strict=True):
                if (addon['icon'] in value['image']) != enabled or (enabled and addon['scaled'] and value['scale'] != '1.3'):
                    raise RuntimeError(f'extension toolbar icon intervention failed: {addon}, {value}')
            resources=m.execute_async_script("""
                const done=arguments[arguments.length-1], urls=[...arguments[0], 'chrome://global/skin/icons/textfox-nonexistent-resource.svg'];
                Promise.all(urls.map(url=>new Promise(resolve=>{
                  const image=new Image(),timer=setTimeout(()=>resolve({url,loaded:false,reason:'timeout'}),5000);
                  image.onload=()=>{clearTimeout(timer);resolve({url,loaded:true,width:image.naturalWidth,height:image.naturalHeight});};
                  image.onerror=()=>{clearTimeout(timer);resolve({url,loaded:false,reason:'error'});};
                  image.src=url;
                }))).then(done);
                """,script_args=[[a['icon'] for a in addons]],sandbox='system')
            negative=resources.pop()
            if negative['loaded'] or negative.get('reason')!='error' or any(not r['loaded'] or r.get('width',0)<=0 or r.get('height',0)<=0 for r in resources):
                raise RuntimeError(f'extension toolbar icon resources unavailable: {resources}, {negative}')
            print(json.dumps({'firefox':meta,'sample':sample,'control':control,'resources':resources}),flush=True)
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


if __name__=='__main__':
    source=(ROOT / 'chrome/icons.css').read_text()
    for extension_id,icon,_ in EXTENSIONS:
        pattern=(r'\[data-extensionid="'+re.escape(extension_id)+r'"\][^{]*\{[^}]*'
                 r'list-style-image\s*:\s*url\(["\']?\./icons/'+re.escape(icon)+r'["\']?\)')
        if not re.search(pattern,source,re.DOTALL):
            raise RuntimeError(f'extension selector or icon changed: {extension_id}, {icon}')
    for nova in (False,True):
        for enabled in (False,True):
            check(nova,enabled)
