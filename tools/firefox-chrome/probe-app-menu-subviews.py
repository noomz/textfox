#!/usr/bin/env python3
"""Inspect app-menu subview icon targets in disposable Firefox profiles."""
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
VIEWS = (
    ('bookmarks','appMenu-bookmarks-button','PanelUI-bookmarks',
     ('panelMenuBookmarkThisPage','panelMenu_searchBookmarks','panelMenu_viewBookmarksToolbar','panelMenu_showAllBookmarks')),
    ('history','appMenu-history-button','PanelUI-history',
     ('appMenuSearchHistory','appMenuClearRecentHistory','PanelUI-historyMore','appMenuRecentlyClosedTabs',
      'appMenuRecentlyClosedWindows','appMenuRestoreSession','appMenu-restoreSession')),
    ('more-tools','appMenu-more-button2','appmenu-moreTools',('appmenu-moreTools-button',)),
    ('help','appMenu-help-button2','PanelUI-helpView',
     ('appMenu_menu_openHelp','appMenu_feedbackPage','appMenu_helpSafeMode','appMenu_troubleShooting',
      'appMenu-report-broken-site-button','appMenu_help_reportSiteIssue','appMenu_help_reportBrokenSite',
      'appMenu_menu_HelpPopup_reportPhishingtoolmenu','appMenu_helpSwitchDevice','appMenu_aboutName')),
)


def check(nova, themed):
    selector_path=os.environ.get('TEXTFOX_SELECTOR_INVENTORY')
    selector_records=None
    selector_query=None
    if selector_path:
        selector_records=[r for r in json.loads(Path(selector_path).read_text()) if r['file']=='chrome/icons.css']
        if not selector_records:
            raise ValueError('selector inventory has no icon records')
        selector_query=runpy.run_path(str(ROOT / 'tools/firefox-chrome/probe-selector-dom.py'))['query']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-app-subviews-') as directory:
        profile = Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        prefs = {'marionette.port':port,'browser.nova.enabled':nova,
                 'toolkit.legacyUserProfileCustomizations.stylesheets':True,
                 'browser.shell.checkDefaultBrowser':False}
        (profile / 'user.js').write_text(''.join(
            f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k,v in prefs.items()))
        env = os.environ.copy()
        env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
        process = subprocess.Popen(['firefox','--marionette','-remote-allow-system-access',
                                    '--new-instance','--profile',str(profile),'about:blank'],
                                   env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        m = None
        try:
            m = connect(port)
            m.set_context('chrome')
            meta = m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled')};
                """, sandbox='system')
            if process.poll() is not None or Path(meta['profile']).resolve() != profile.resolve() or meta['nova'] != nova:
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            deadline=time.monotonic()+20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished',sandbox='system'):
                if time.monotonic() >= deadline:
                    state=m.execute_script("return {startup:gBrowserInit.delayedStartupFinished,uri:gBrowser.currentURI.spec}",sandbox='system')
                    raise RuntimeError(f'local page or Firefox startup did not finish: {state}')
                time.sleep(.1)
            m.set_context('content')
            m.navigate('about:robots')
            if m.execute_script('return location.href') != 'about:robots':
                raise RuntimeError('local page did not load')
            m.set_context('chrome')
            states={}
            for name, trigger, view_id, ids in VIEWS:
                m.execute_script('PanelUI.show()',sandbox='system')
                deadline=time.monotonic()+15
                while m.execute_script("return document.getElementById('appMenu-popup').state",sandbox='system') != 'open':
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'app menu did not open for {name}')
                    time.sleep(.1)
                m.find_element('id',trigger).click()
                deadline=time.monotonic()+15
                while True:
                    state=m.execute_script("""
                        const view=document.getElementById(arguments[0]); if(!view)return null;
                        const r=view.getBoundingClientRect(),s=getComputedStyle(view);
                        if(r.width <= 0 || r.height <= 0 || s.visibility !== 'visible')return null;
                        return {viewId:view.id,viewWidth:r.width,viewHeight:r.height,
                          items:Object.fromEntries(arguments[1].map(id=>{
                            const e=document.getElementById(id); if(!e)return [id,null];
                            const st=getComputedStyle(e),rect=e.getBoundingClientRect();
                            return [id,{inView:view.contains(e),hidden:e.hidden,disabled:e.disabled,display:st.display,
                              visibility:st.visibility,width:rect.width,height:rect.height,
                              image:st.listStyleImage,starred:e.hasAttribute('starred')}];
                          })),
                          developerItems:[...view.querySelectorAll('#appmenu-developer-tools-view .subviewbutton')].map(e=>{
                            const st=getComputedStyle(e),rect=e.getBoundingClientRect(), parent=e.parentElement;
                            const siblings=[...parent.children], index=siblings.indexOf(e);
                            return {id:e.id,label:e.getAttribute('label'),command:e.getAttribute('command'),
                              childIndex:index+1,childCount:siblings.length,display:st.display,visibility:st.visibility,
                              width:rect.width,height:rect.height,image:st.listStyleImage};
                          })};
                        """,script_args=[view_id,list(ids)],sandbox='system')
                    if state:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'app menu subview {name} did not open: {state}')
                    time.sleep(.1)
                visible=[item for item_id,item in state['items'].items() if item_id != 'appMenu_help_reportBrokenSite' and item and item['inView'] and not item['hidden']
                         and item['display'] != 'none' and item['visibility'] == 'visible'
                         and item['width'] > 0 and item['height'] > 0]
                if not visible:
                    raise RuntimeError(f'app menu subview {name} has no visible icon candidates: {state}')
                visible_developer=[item for item in state['developerItems'] if item['display'] != 'none'
                                   and item['visibility'] == 'visible' and item['width'] > 0 and item['height'] > 0]
                if name == 'more-tools' and not visible_developer:
                    raise RuntimeError(f'developer-tools fixture is unavailable: {state}')
                if name == 'help':
                    report=state['items']['appMenu_help_reportBrokenSite']
                    if not report or not report['inView'] or report['hidden'] or report['width']<=0 or report['height']<=0:
                        raise RuntimeError(f'current report-broken-site item unavailable: {report}')
                raws=sorted({item['image'] for item in [*visible,*visible_developer] if item['image'] != 'none'})
                resources=m.execute_async_script("""
                    const done=arguments[arguments.length-1], values=[...arguments[0],'url("chrome://global/skin/icons/textfox-nonexistent-resource.svg")'];
                    Promise.all(values.map(raw=>new Promise(resolve=>{
                      const match=raw.match(/^url\\(["']?(.*?)["']?\\)$/);
                      if(!match){resolve({raw,loaded:false,reason:'not a single URL'});return;}
                      const image=new Image(),timer=setTimeout(()=>resolve({raw,loaded:false,reason:'timeout'}),5000);
                      image.onload=()=>{clearTimeout(timer);resolve({raw,loaded:true,width:image.naturalWidth,height:image.naturalHeight});};
                      image.onerror=()=>{clearTimeout(timer);resolve({raw,loaded:false,reason:'error'});};
                      image.src=match[1];
                    }))).then(done);
                    """,script_args=[raws],sandbox='system')
                negative=resources[-1]
                if negative['loaded'] or negative.get('reason') != 'error':
                    raise RuntimeError(f'icon-resource negative control failed: {negative}')
                state['resources']=resources[:-1]
                if selector_query:
                    state['selectorRecords']=selector_query(m,selector_records)
                if themed and (any(item['image'] == 'none' for item in [*visible,*visible_developer])
                               or any(not item['loaded'] or item.get('width',0) <= 0 or item.get('height',0) <= 0
                                      for item in state['resources'])):
                    raise RuntimeError(f'app menu icon resources unavailable in {name}: {state}')
                if name == 'bookmarks':
                    inserted=m.execute_async_script("""
                        const done=arguments[arguments.length-1];
                        PlacesUtils.bookmarks.insert({parentGuid:PlacesUtils.bookmarks.unfiledGuid,
                          url:'about:robots',title:'Textfox starred fixture'}).then(item=>done(item.guid),error=>done({error:String(error)}));
                        """,sandbox='system')
                    if not isinstance(inserted,str):
                        raise RuntimeError(f'could not seed starred bookmark: {inserted}')
                    deadline=time.monotonic()+15
                    while True:
                        starred=m.execute_script("""
                            const e=document.getElementById('panelMenuBookmarkThisPage');
                            return {starred:e.hasAttribute('starred'),image:getComputedStyle(e).listStyleImage};
                            """,sandbox='system')
                        if starred['starred']:
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'starred bookmark state did not update: {starred}')
                        time.sleep(.1)
                    if themed and ('bookmark-hollow.svg' not in state['items']['panelMenuBookmarkThisPage']['image']
                                   or 'bookmark.svg' not in starred['image']):
                        raise RuntimeError(f'bookmark icon did not change with starred state: {starred}')
                    state['starredControl']=starred
                    if selector_query:
                        state['starredSelectorRecords']=selector_query(m,selector_records)
                        matched=[r for r in state['starredSelectorRecords']
                                 if r['selector'] == '#panelMenuBookmarkThisPage[starred]']
                        if len(matched) != 1 or matched[0]['status'] != 'structural-match':
                            raise RuntimeError(f'starred icon selector did not match: {matched}')
                states[name]=state
                m.execute_script('PanelUI.hide()',sandbox='system')
                deadline=time.monotonic()+10
                while m.execute_script("return document.getElementById('appMenu-popup').state",sandbox='system') != 'closed':
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'app menu did not close after {name}')
                    time.sleep(.1)
            print(json.dumps({'firefox':meta,'themed':themed,'views':states}),flush=True)
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
            check(nova,themed)
