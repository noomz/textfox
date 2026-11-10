#!/usr/bin/env python3
"""Inspect nested Bitwarden-style context icon selectors with a synthetic add-on.

Requires tinycss2 1.4.0 and the live-tool venv. No real extension code is used.
"""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile

import tinycss2
from marionette_driver.keys import Keys

from ff_inspect import connect

ROOT=Path(__file__).resolve().parents[2]
EXTENSION_ID='{446900e4-71c2-419f-a6a7-df9c091e268b}'
ROOT_ID='_446900e4-71c2-419f-a6a7-df9c091e268b_-menuitem-_root'
PREF='shyfox.enable.ext.mono.context.icons'
CHILDREN=('autofill','copy-username','copy-password','autofill-identity','autofill-card',
          'generate-password','copy-identifier','create-fixture')
ICONS=('chrome://browser/skin/login.svg','edit-copy.svg','password.svg',
       'chrome://browser/skin/fxa/avatar-empty.svg','credit-card.svg',
       'reload-auto.svg','edit-copy.svg','chrome://global/skin/icons/plus.svg')
EXTRAS=(('uBlock0@raymondhill.net','uBlock0-fixture','ublock0_raymondhill_net-menuitem-_uBlock0-','ublock.svg'),
        ('simple-translate@sienori','fixture','simple-translate_sienori-menuitem-','translate.svg'),
        ('{74145f27-f039-47ce-a470-a662b129930a}','copy-link-to-clipboard','_74145f27-f039-47ce-a470-a662b129930a_-menuitem-_copy-link-to-clipboard','eraser.svg'),
        ('{b9db16a4-6edc-47ec-a1f4-b86292ed211d}','vdh-top','_b9db16a4-6edc-47ec-a1f4-b86292ed211d_-menuitem-_vdh-top','video-download-helper.svg'))


def modern_css(profile):
    nodes=tinycss2.parse_stylesheet((ROOT/'chrome/icons.css').read_text(),skip_whitespace=True,skip_comments=True)
    expected=f'(-moz-bool-pref: "{PREF}")'
    matches=[n for n in nodes if n.type=='at-rule' and n.lower_at_keyword=='media'
             and tinycss2.serialize(n.prelude).strip()==expected]
    if len(matches)!=1:
        raise RuntimeError('extension context media rule changed; update diagnostic')
    icons=(profile/'chrome/icons').as_uri()+'/'
    return f'@media -moz-pref("{PREF}") {{ '+tinycss2.serialize(matches[0].content).replace('./icons/',icons)+' }'


def check(nova,enabled):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/icons.css']
        if not selector_records:
            raise ValueError('selector inventory has no icon records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-extension-context-') as directory:
        profile=Path(directory)
        shutil.copytree(ROOT/'chrome',profile/'chrome')
        addon=profile/'fixture.xpi'
        manifest={'manifest_version':2,'name':'Textfox nested menu fixture','version':'1.0',
                  'browser_specific_settings':{'gecko':{'id':EXTENSION_ID}},
                  'permissions':['menus'],'icons':{'16':'fixture.svg'},'background':{'scripts':['background.js']}}
        js=('browser.menus.create({id:"root",title:"Textfox menu fixture",contexts:["link"],icons:{"16":"fixture.svg"}});'
            +''.join('browser.menus.create('+json.dumps({'id':child,'parentId':'root','title':f'Textfox {child}',
                                                          'contexts':['link']})+');' for child in CHILDREN))
        with zipfile.ZipFile(addon,'w') as archive:
            archive.writestr('manifest.json',json.dumps(manifest))
            archive.writestr('fixture.svg','<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="black" d="M0 0h16v16H0z"/></svg>')
            archive.writestr('background.js',js)
        archives=[(EXTENSION_ID,addon)]
        for index,(addon_id,menu_id,_,_) in enumerate(EXTRAS):
            extra=profile/f'extra-{index}.xpi'
            extra_manifest={'manifest_version':2,'name':f'Textfox context fixture {index}','version':'1.0',
                            'browser_specific_settings':{'gecko':{'id':addon_id}},'permissions':['menus'],
                            'icons':{'16':'fixture.svg'},'background':{'scripts':['background.js']}}
            with zipfile.ZipFile(extra,'w') as archive:
                archive.writestr('manifest.json',json.dumps(extra_manifest))
                archive.writestr('fixture.svg','<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="black" d="M0 0h16v16H0z"/></svg>')
                archive.writestr('background.js','browser.menus.create('+json.dumps({'id':menu_id,'title':f'Textfox extra {index}','contexts':['link']})+');')
            archives.append((addon_id,extra))
        prefs={'marionette.port':port,'browser.nova.enabled':nova,PREF:enabled,
               'toolkit.legacyUserProfileCustomizations.stylesheets':True,'browser.shell.checkDefaultBrowser':False}
        (profile/'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k,v in prefs.items()))
        env=os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS']='1'
        process=subprocess.Popen(['firefox','--marionette','-remote-allow-system-access','--new-instance',
                                  '--profile',str(profile),'about:blank'],env=env,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        m=None
        try:
            m=connect(port)
            m.set_context('chrome')
            meta=m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,version:Services.appinfo.version,
                  build:Services.appinfo.appBuildID,nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  iconPref:Services.prefs.getBoolPref('shyfox.enable.ext.mono.context.icons')};
                """,sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve()!=profile.resolve()
                    or meta['nova']!=nova or meta['iconPref']!=enabled):
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            for addon_id,archive in archives:
                installed=m.execute_async_script("""
                    const done=arguments[arguments.length-1],file=Cc['@mozilla.org/file/local;1'].createInstance(Ci.nsIFile);
                    file.initWithPath(arguments[0]);
                    AddonManager.installTemporaryAddon(file).then(a=>done({id:a.id,active:a.isActive}),e=>done({error:String(e)}));
                    """,script_args=[str(archive)],sandbox='system')
                if installed.get('id')!=addon_id or not installed.get('active'):
                    raise RuntimeError(f'fixture extension failed to install: {installed}')
            m.set_context('content')
            m.navigate('data:text/html,<a href="https://example.invalid/">fixture link</a>')
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).click(m.find_element('css selector','a'),button=2).perform()
            m.set_context('chrome')
            deadline=time.monotonic()+15
            while not m.execute_script("const p=document.getElementById('contentAreaContextMenu'),e=document.getElementById(arguments[0]),r=e?.getBoundingClientRect(); return p.state==='open' && !!r && !e.hidden && r.width>0 && r.height>0",script_args=[ROOT_ID],sandbox='system'):
                if time.monotonic()>=deadline:
                    raise RuntimeError('extension root menu did not appear')
                time.sleep(.1)
            deadline=time.monotonic()+15
            while not m.execute_script("return arguments[0].every(fragment=>!![...document.querySelectorAll('#contentAreaContextMenu [id]')].find(e=>e.id.includes(fragment)))",script_args=[[entry[2] for entry in EXTRAS]],sandbox='system'):
                if time.monotonic()>=deadline:
                    raise RuntimeError('extra extension menu fixtures did not initialize')
                time.sleep(.1)
            root=m.find_element('id',ROOT_ID)
            m.actions.sequence('pointer','mouse',{'pointerType':'mouse'}).pointer_move(0,0,origin=root).perform()
            deadline=time.monotonic()+15
            while not m.execute_script("return document.getElementById(arguments[0])?.querySelector('menupopup')?.state === 'open'",script_args=[ROOT_ID],sandbox='system'):
                if time.monotonic()>=deadline:
                    raise RuntimeError('extension submenu did not open')
                time.sleep(.1)
            measure="""
                const root=document.getElementById(arguments[0]), popup=root.querySelector('menupopup');
                const icon=root.querySelector('.menu-iconic-icon'), shadowIcon=root.shadowRoot?.querySelector('.menu-iconic-icon');
                const inspect=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();
                  return {id:e.id,tag:e.localName,hidden:e.hidden,display:s.display,visibility:s.visibility,
                    width:r.width,height:r.height,image:s.backgroundImage};};
                return {root:inspect(root),hasShadow:!!root.shadowRoot,
                  iconOpacity:icon ? getComputedStyle(icon).opacity : null,
                  shadowIconOpacity:shadowIcon ? getComputedStyle(shadowIcon).opacity : null,
                  children:[...popup.children].map(inspect),
                  legacy:matchMedia('(-moz-bool-pref: "shyfox.enable.ext.mono.context.icons")').matches,
                  modern:matchMedia('-moz-pref("shyfox.enable.ext.mono.context.icons")').matches};
                """
            baseline=m.execute_script(measure,script_args=[ROOT_ID],sandbox='system')
            extra_baseline=m.execute_script("""
                return arguments[0].map(fragment=>{const e=[...document.querySelectorAll('#contentAreaContextMenu [id]')].find(node=>node.id.includes(fragment));
                  const s=getComputedStyle(e),r=e.getBoundingClientRect();return {id:e.id,hidden:e.hidden,display:s.display,visibility:s.visibility,
                    width:r.width,height:r.height,image:s.backgroundImage,hasShadow:!!e.shadowRoot,
                    lightIcon:!!e.querySelector('.menu-iconic-icon'),shadowIcon:!!e.shadowRoot?.querySelector('.menu-iconic-icon')};});
                """,script_args=[[entry[2] for entry in EXTRAS]],sandbox='system')
            if any(e['hidden'] or e['display']=='none' or e['visibility']!='visible' or e['width']<=0 or e['height']<=0 for e in extra_baseline):
                raise RuntimeError(f'extra extension menu fixtures not visible: {extra_baseline}')
            if (baseline['modern']!=enabled or len(baseline['children'])!=len(CHILDREN)
                    or any(item['hidden'] or item['display']=='none' or item['visibility']!='visible'
                           or item['width']<=0 or item['height']<=0 for item in baseline['children'])):
                raise RuntimeError(f'extension context fixture incomplete: {baseline}')
            control=m.execute_script("""
                const uri='data:text/css,'+encodeURIComponent(arguments[0]);
                window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                try {
                  const root=document.getElementById(arguments[1]), icon=root.querySelector('.menu-iconic-icon'),
                    shadowIcon=root.shadowRoot?.querySelector('.menu-iconic-icon');
                  return {root:getComputedStyle(root).backgroundImage,iconOpacity:icon ? getComputedStyle(icon).opacity : null,
                    shadowIconOpacity:shadowIcon ? getComputedStyle(shadowIcon).opacity : null,
                    children:[...root.querySelector('menupopup').children].map(e=>({id:e.id,image:getComputedStyle(e).backgroundImage}))};
                } finally { window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET); }
                """,script_args=[modern_css(profile),ROOT_ID],sandbox='system')
            if enabled:
                if ('bitwarden.svg' not in control['root']
                        or any(not item['id'].endswith('-menuitem-_'+name) or icon not in item['image']
                               for item,name,icon in zip(control['children'],CHILDREN,ICONS,strict=True))):
                    raise RuntimeError(f'extension context modern-sheet control failed: {control}')
            if not enabled and (control['root']!=baseline['root']['image'] or any(c['image']!=b['image'] for c,b in zip(control['children'],baseline['children'],strict=True))):
                raise RuntimeError(f'disabled extension context control changed output: {baseline}, {control}')
            extra_control=m.execute_script("""
                const uri='data:text/css,'+encodeURIComponent(arguments[0]);
                window.windowUtils.loadSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);
                try{return arguments[1].map(fragment=>{const e=[...document.querySelectorAll('#contentAreaContextMenu [id]')].find(node=>node.id.includes(fragment));return {id:e.id,image:getComputedStyle(e).backgroundImage};});}
                finally{window.windowUtils.removeSheetUsingURIString(uri,Ci.nsIDOMWindowUtils.USER_SHEET);}
                """,script_args=[modern_css(profile),[entry[2] for entry in EXTRAS]],sandbox='system')
            if any((icon in item['image'])!=enabled for item,(*_,icon) in zip(extra_control,EXTRAS,strict=True)):
                raise RuntimeError(f'extra extension context controls failed: {extra_control}')
            resources=[]
            if enabled:
                resources=m.execute_async_script("""
                    const done=arguments[arguments.length-1], raws=[...arguments[0], 'url("chrome://global/skin/icons/textfox-nonexistent-resource.svg")'];
                    Promise.all(raws.map(raw=>new Promise(resolve=>{
                      const match=raw.match(/^url\\(["']?(.*?)["']?\\)$/);
                      if(!match){resolve({raw,loaded:false,reason:'not a single URL'});return;}
                      const image=new Image(),timer=setTimeout(()=>resolve({raw,loaded:false,reason:'timeout'}),5000);
                      image.onload=()=>{clearTimeout(timer);resolve({raw,loaded:true,width:image.naturalWidth,height:image.naturalHeight});};
                      image.onerror=()=>{clearTimeout(timer);resolve({raw,loaded:false,reason:'error'});};
                      image.src=match[1];
                    }))).then(done);
                    """,script_args=[[control['root'],*[x['image'] for x in control['children']],*[x['image'] for x in extra_control]]],sandbox='system')
                negative=resources.pop()
                if negative['loaded'] or negative.get('reason')!='error' or any(not r['loaded'] or r.get('width',0)<=0 or r.get('height',0)<=0 for r in resources):
                    raise RuntimeError(f'extension context icon resources unavailable: {resources}, {negative}')
            selector_results=selector_query(m,selector_records) if selector_query else []
            if selector_query:
                root_records=[r for r in selector_results if r['selector']=='#'+ROOT_ID]
                if len(root_records)!=1 or root_records[0]['status']!='structural-match':
                    raise RuntimeError(f'extension root selector inventory did not match: {root_records}')
            m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
            m.actions.sequence('key','keyboard').send_keys(Keys.ESCAPE).perform()
            print(json.dumps({'firefox':meta,'baseline':baseline,'modernControl':control,'resources':resources,
                              'extraBaseline':extra_baseline,'extraControl':extra_control,'selectorRecords':selector_results}),flush=True)
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
    for nova in (False,True):
        for enabled in (False,True):
            check(nova,enabled)
