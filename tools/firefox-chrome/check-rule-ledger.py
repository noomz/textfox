#!/usr/bin/env python3
"""Route every tracked CSS rule record to its T2 evidence group.

Requires tinycss2 1.4.0. Routing is not proof that selectors match or work.
"""
import collections
import json
from pathlib import Path
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = runpy.run_path(str(ROOT / 'tools/firefox-chrome/inventory-css.py'))['inventory']
SECTIONS = {
    'chrome/browser.css':'Browser content and split view',
    'chrome/content/about.css':'New-tab and `about:` content',
    'chrome/content/newtab.css':'New-tab and `about:` content',
    'chrome/defaults.css':'Shared root and container rules',
    'chrome/findbar.css':'Findbar',
    'chrome/menus.css':'Menus and panels',
    'chrome/navbar.css':'Toolbar',
    'chrome/overwrites.css':'Shared root and container rules',
    'chrome/sidebar.css':'Sidebar',
    'chrome/tabs.css':'Tabs',
    'chrome/urlbar.css':'URL bar',
    'chrome/userChrome.css':'Shared root and container rules',
    'chrome/userContent.css':'New-tab and `about:` content',
}


def route(file, rule):
    if file != 'chrome/icons.css':
        if file not in SECTIONS:
            raise ValueError(f'unmapped CSS file: {file}')
        return SECTIONS[file], 'component'
    context = rule['context']
    media = [c for c in context if c.startswith('@')]
    if not media:
        return 'Shared icon scope', 'unconditional'
    if len(media) != 1:
        raise ValueError(f'unmapped icon media context: {context}')
    if media[0] == '@media (-moz-bool-pref: "shyfox.enable.ext.mono.toolbar.icons")':
        return 'Shared icon scope', 'extension-toolbar'
    if media[0] == '@media (-moz-bool-pref: "shyfox.enable.ext.mono.context.icons")':
        return 'Shared icon scope', 'extension-context'
    if media[0] != '@media (-moz-bool-pref: "shyfox.enable.context.menu.icons")':
        raise ValueError(f'unmapped icon media context: {context}')
    anchor = next((c for c in context if not c.startswith('@')), rule['selector'])
    if '#contentAreaContextMenu' in anchor:
        group = 'content-context'
    elif '#toolbar-context-menu' in anchor or '#unified-extensions-context-menu' in anchor:
        group = 'toolbar-extension-context'
    elif '#placesContext' in anchor:
        group = 'places-context'
    elif '#downloadsContextMenu' in anchor:
        group = 'downloads-context'
    elif '#sidebarMenu-popup' in anchor:
        group = 'sidebar-context'
    elif '#BMB_bookmarksPopup' in anchor or '#PlacesToolbar' in anchor:
        group = 'bookmarks-popup'
    else:
        raise ValueError(f'unmapped context-icon anchor: {anchor}')
    return 'Shared icon scope', group


def main():
    tracked = subprocess.check_output(['git', 'ls-files', '-z', 'chrome'], cwd=ROOT).decode().split('\0')
    ledger = (ROOT / 'T2-INVENTORY.md').read_text()
    records = []
    for file in tracked:
        if not file.endswith('.css') or Path(file).name == 'config.css':
            continue
        result = INVENTORY(ROOT / file)
        if file != 'chrome/icons.css' and file not in SECTIONS:
            raise ValueError(f'unmapped CSS file: {file}')
        for rule in result['rules']:
            section, group = route(file, rule)
            if f'## {section}\n' not in ledger:
                raise ValueError(f'ledger section missing: {section}')
            records.append({'file':file, **rule, 'ledgerSection':section, 'evidenceGroup':group})
    counts = collections.Counter((r['ledgerSection'], r['evidenceGroup']) for r in records)
    print(json.dumps({'recordCount':len(records),
                      'groups':[{'section':section,'group':group,'records':count}
                                for (section,group),count in sorted(counts.items())],
                      'records':records}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
