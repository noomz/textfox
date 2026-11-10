#!/usr/bin/env python3
"""Check rule-to-ledger routing, including nested icon rules and unknown scopes."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('rule_ledger', HERE / 'check-rule-ledger.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def record(selector, context):
    return {'line':1, 'context':context, 'selector':selector}

assert module.route('chrome/tabs.css', record('&:hover', ['#TabsToolbar'])) == ('Tabs', 'component')
media = '@media (-moz-bool-pref: "shyfox.enable.context.menu.icons")'
assert module.route('chrome/icons.css', record('#placesContext', [media])) == ('Shared icon scope', 'places-context')
assert module.route('chrome/icons.css', record('#placesContext_open\\:newtab', [media, '#placesContext'])) == ('Shared icon scope', 'places-context')
assert module.route('chrome/icons.css', record('context-openlinkincontainertab', [media, '#contentAreaContextMenu'])) == ('Shared icon scope', 'content-context')
assert module.route('chrome/icons.css', record('#appMenu-downloads-button', [])) == ('Shared icon scope', 'unconditional')
for file, context in [('chrome/new-file.css', []), ('chrome/icons.css', ['@media (unknown)']),
                      ('chrome/icons.css', ['@media (-moz-bool-pref: "shyfox.enable.context.menu.icons") or (unknown)'])]:
    try:
        module.route(file, record('x', context))
    except ValueError:
        pass
    else:
        raise AssertionError(f'unknown file or media was silently routed: {file}, {context}')
print('Rule ledger routing fixture passed')
