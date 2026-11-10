#!/usr/bin/env python3
"""Observe app-menu bookmark creation in disposable local-page profiles.

An absent record is reported as an observation, not assumed to identify cause.
"""
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


def bookmark(m, url):
    return m.execute_async_script("""
        const done=arguments[arguments.length-1];
        PlacesUtils.bookmarks.fetch({url:arguments[0]}).then(item=>done(item ? {guid:item.guid,url:item.url.href} : null),
          error=>done({error:String(error)}));
        """,script_args=[url],sandbox='system')


def check(nova, themed, page, show_icons=False, show_dialog=True):
    if show_icons and not themed:
        raise ValueError('icon control requires textfox')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tf-bookmark-action-') as directory:
        profile=Path(directory)
        if themed:
            shutil.copytree(ROOT / 'chrome',profile / 'chrome')
            if show_icons:
                (profile / 'chrome' / 'config.css').write_text(':root { --tf-display-urlbar-icons: flex; }\n')
        local=profile / 'fixture.html'
        local.write_text('<!doctype html><title>Textfox bookmark fixture</title><p>local fixture</p>')
        url='about:robots' if page == 'about' else local.as_uri()
        prefs={'marionette.port':port,'browser.nova.enabled':nova,
               'browser.bookmarks.editDialog.showForNewBookmarks':show_dialog,
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
            m.set_context('chrome')
            meta=m.execute_script("""
                return {profile:Services.dirsvc.get('ProfD',Ci.nsIFile).path,
                  version:Services.appinfo.version,build:Services.appinfo.appBuildID,
                  nova:Services.prefs.getBoolPref('browser.nova.enabled'),
                  showEditDialog:Services.prefs.getBoolPref('browser.bookmarks.editDialog.showForNewBookmarks'),
                  defaultShowEditDialog:Services.prefs.getDefaultBranch('').getBoolPref('browser.bookmarks.editDialog.showForNewBookmarks')};
                """,sandbox='system')
            if (process.poll() is not None or Path(meta['profile']).resolve()!=profile.resolve()
                    or meta['nova']!=nova or meta['showEditDialog']!=show_dialog):
                raise RuntimeError(f'unexpected Firefox process, profile, or preferences: {meta}')
            deadline=time.monotonic()+20
            while not m.execute_script('return gBrowserInit.delayedStartupFinished',sandbox='system'):
                if time.monotonic()>=deadline:
                    raise RuntimeError('Firefox startup did not finish')
                time.sleep(.1)
            m.set_context('content')
            m.navigate(url)
            if m.execute_script('return location.href') != url:
                raise RuntimeError('local bookmark fixture did not load')
            m.set_context('chrome')
            before=bookmark(m,url)
            if before is not None:
                raise RuntimeError(f'bookmark fixture was not fresh: {before}')
            m.execute_script('PanelUI.show()',sandbox='system')
            deadline=time.monotonic()+15
            while m.execute_script("return document.getElementById('appMenu-popup').state",sandbox='system') != 'open':
                if time.monotonic()>=deadline:
                    raise RuntimeError('app menu did not open')
                time.sleep(.1)
            m.find_element('id','appMenu-bookmarks-button').click()
            deadline=time.monotonic()+15
            while not m.execute_script("const e=document.getElementById('panelMenuBookmarkThisPage'),r=e?.getBoundingClientRect(); return !!e && !e.disabled && !!r && r.width > 0 && r.height > 0",sandbox='system'):
                if time.monotonic()>=deadline:
                    raise RuntimeError('bookmark action unavailable')
                time.sleep(.1)
            initial=m.execute_script("""
                const e=document.getElementById('panelMenuBookmarkThisPage'), box=document.getElementById('star-button-box');
                const s=getComputedStyle(box),r=box.getBoundingClientRect();
                let anchor=null,anchorError=null;
                try { anchor=BookmarkingUI.anchor; } catch(error) { anchorError=String(error); }
                const ar=anchor?.getBoundingClientRect();
                return {starred:e.hasAttribute('starred'),image:getComputedStyle(e).listStyleImage,
                  starBox:{display:s.display,visibility:s.visibility,width:r.width,height:r.height},anchorError,
                  anchor:anchor && {id:anchor.id,display:getComputedStyle(anchor).display,width:ar.width,height:ar.height}};
                """,sandbox='system')
            if initial['starred']:
                raise RuntimeError(f'bookmark UI did not start unstarred: {initial}')
            m.find_element('id','panelMenuBookmarkThisPage').click()
            deadline=time.monotonic()+10
            done_clicked=False
            record=None
            while True:
                status=m.execute_script("""
                    const edit=document.getElementById('editBookmarkPanel');
                    const done=document.getElementById('editBookmarkPanelDoneButton');
                    let anchorId=null,anchorError=null;
                    try { anchorId=BookmarkingUI.anchor?.id || null; } catch(error) { anchorError=String(error); }
                    return {uri:gBrowser.currentURI.spec,starred:document.getElementById('star-button')?.hasAttribute('starred'),
                      anchorId,anchorError,menuState:document.getElementById('appMenu-popup').state,editState:edit?.state || null,
                      doneVisible:!!done && !!done.getBoundingClientRect().width && !!done.getBoundingClientRect().height};
                    """,sandbox='system')
                if status['editState']=='open' and status['doneVisible'] and not done_clicked:
                    m.find_element('id','editBookmarkPanelDoneButton').click()
                    done_clicked=True
                record=bookmark(m,url)
                if record or time.monotonic()>=deadline:
                    break
                time.sleep(.2)
            if isinstance(record,dict) and 'error' in record:
                raise RuntimeError(f'bookmark lookup failed: {record}')
            if status['uri'] != url:
                raise RuntimeError(f'bookmark action changed selected page: {status}')
            if ((not themed or show_icons or not show_dialog) and not record
                    or show_dialog and (not themed or show_icons) and (not done_clicked or initial['anchorError'])):
                raise RuntimeError(f'bookmark positive control failed: {initial}, {status}, {record}')
            print(json.dumps({'firefox':meta,'themed':themed,'showIcons':show_icons,'page':page,'initial':initial,
                              'after':status,'doneClicked':done_clicked,'bookmark':record}),flush=True)
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
        for themed,show_icons,show_dialog in ((False,False,True),(True,False,True),
                                              (True,True,True),(True,False,False)):
            for page in ('about','file'):
                check(nova,themed,page,show_icons,show_dialog)
