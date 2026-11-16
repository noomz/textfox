#!/usr/bin/env python3
"""Fixtures for check-cascade-order.py.

The cascade model is pure logic, so it can be pinned without Firefox: each case
builds a tiny synthetic chrome/ tree and a synthetic wrapTextfox.nix, then asserts
what the chain order and the comparison must report. Exit 0 means every case held.

Run: python3 tools/firefox-chrome/test-cascade-order.py
"""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cascade', HERE / 'check-cascade-order.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, got, want):
    if got != want:
        failures.append(name)
        print(f'FAIL {name}\n  got  {got!r}\n  want {want!r}')
    else:
        print(f'ok   {name}')


def tree(files, wrap):
    """Point the module at a synthetic chrome/ tree and wrap derivation."""
    root = pathlib.Path(tempfile.mkdtemp())
    css = root / 'chrome'
    css.mkdir(parents=True)
    for rel, text in files.items():
        path = css / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    mod.CSS = css
    mod.WRAP = root / 'wrapTextfox.nix'
    mod.WRAP.write_text(wrap)
    return root


def sections(body_chrome, body_content=''):
    return f'''
      ### USERCHROME
{body_chrome}
      ### USERCONTENT
{body_content}
'''


def cat(*names):
    return '\n'.join(f'      cat "$src/{n}" >> "$out/x.css"' for n in names)


# --- Case 1: import order decides between two imported sheets. ---------------
# Manual: @import a then b, so b wins. Wrap cats b then a, so a wins.
tree({'userChrome.css': '@import url("a.css");\n@import url("b.css");\ny { width: 1px; }\n',
      'a.css': 'x { color: from-a; }\n',
      'b.css': 'x { color: from-b; }\n'},
     sections(cat('b.css', 'a.css', 'userChrome.css')))
chain, keyed, _ = mod.manual_chain('userChrome.css')
check('manual chain order', chain, ['userChrome.css', 'a.css', 'b.css'])
check('manual winner follows import order', mod.winner(keyed[('x', 'color')])['sheet'], 'b.css')
wkeyed, _ = mod.wrap_chain(mod.wrap_members()['userchrome'])
check('wrap winner follows cat order', mod.winner(wkeyed[('x', 'color')])['sheet'], 'a.css')
check('reversed order is a hazard',
      [(h['selector'], h['property'], h['manualWinner']['sheet'], h['wrapWinner']['sheet'])
       for h in mod.compare(keyed, wkeyed)[0]],
      [('x', 'color', 'b.css', 'a.css')])

# --- Case 2: an imported sheet precedes the importing sheet's own rules, but in
# the generated file the entry's rules land in place and its imports are ignored.
tree({'userChrome.css': '@import url("a.css");\n@import url("config.css");\nx { color: manual-body; }\n',
      'a.css': 'x { color: from-a; }\n',
      'overwrites.css': 'html { background: none; }\n'}, '''      ### USERCHROME
      cat "$src/overwrites.css" >> "$out/userChrome.css"
      cat "$src/userChrome.css" >> "$out/userChrome.css"
      cat "$src/a.css" >> "$out/userChrome.css"
      cat "$configCssPath" >> "$out/userChrome.css"
      ### USERCONTENT
      cat "$src/userContent.css" >> "$out/userContent.css"
''')
chain, keyed, diag = mod.manual_chain('userChrome.css')
check('absent config.css is an empty sheet, not a crash',
      [d['kind'] for d in diag if d['sheet'] == 'config.css'], ['missing-sheet-empty'])
check('manual winner body-vs-import is the body rule',
      mod.winner(keyed[('x', 'color')])['sheet'], 'userChrome.css')
wkeyed, wdiag = mod.wrap_chain(mod.wrap_members()['userchrome'])
check('member imports after an earlier member are ignored',
      sorted({d['kind'] for d in wdiag}), ['late-import-ignored', 'member-not-supplied'])
check('generated chain members in literal order',
      [n for k, n in mod.wrap_members()['userchrome']],
      ['overwrites.css', 'userChrome.css', 'a.css', 'configCss'])
check('the entry\'s own imports are reported as ignored, not spliced',
      sorted(d['statement'] for d in wdiag if d['kind'] == 'late-import-ignored'),
      sorted(['@import url("a.css")', '@import url("config.css")']))
check('wrap winner body-vs-import is the concatenated member',
      mod.winner(wkeyed[('x', 'color')])['sheet'], 'a.css')
check('body-vs-import inversion is a hazard',
      [(h['selector'], h['property'], h['manualWinner']['sheet'], h['wrapWinner']['sheet'])
       for h in mod.compare(keyed, wkeyed)[0]],
      [('x', 'color', 'userChrome.css', 'a.css')])
check('first member may import validly',
      sorted({d['kind'] for d in mod.wrap_chain(mod.wrap_members()['userchrome'])[1]
              if d['sheet'] == 'overwrites.css'}), [])

# --- Case 3: !important wins regardless of chain position. -------------------
tree({'userChrome.css': '@import url("a.css");\nx { color: plain; }\n',
      'a.css': 'x { color: flagged !important; }\n'}, sections(cat('userChrome.css', 'a.css')))
_, keyed, _ = mod.manual_chain('userChrome.css')
check('!important beats a later plain declaration',
      mod.winner(keyed[('x', 'color')])['value'], 'flagged !important')

# --- Case 4: nested selector resolution across comma lists and &. -----------
check('comma parent expands across the child',
      mod.resolve_selector('a, b', '&::before'), 'a::before, b::before')
check('comma child list resolves per part',
      mod.resolve_selector('#nav-bar', 'x, y'), '#nav-bar x, #nav-bar y')
check('descendant nesting without &',
      mod.resolve_selector('.split-view-panel', 'button'), '.split-view-panel button')
tree({'userChrome.css': '@media (prefers-color-scheme: dark) {\n  a, b { &::before { color: d; } }\n}\n'},
     sections(cat('userChrome.css')))
_, keyed, _ = mod.manual_chain('userChrome.css')
check('comma parent inside @media keys correctly', sorted(keyed), [('a::before, b::before', 'color')])
recs = []
mod.walk_sheet(mod.strip_comments('toolbar:-moz-pref("a, b") { x { color: q; } }'), 's', recs, [])
check('quoted comma inside a functional pseudo-selector survives',
      sorted({(r['selector'], r['property']) for r in recs}),
      [('toolbar:-moz-pref("a, b") x', 'color')])

# --- Case 5: identical conditional declarations raise nothing. --------------
tree({'userChrome.css': '@media (-moz-pref("p")) { x { color: c; } }\n'},
     sections(cat('userChrome.css')))
_, keyed, _ = mod.manual_chain('userChrome.css')
wkeyed, _ = mod.wrap_chain(mod.wrap_members()['userchrome'])
check('identical conditional chains raise nothing', mod.compare(keyed, wkeyed)[1], [])

# --- Case 6: chain drift (a manual import with no member) exits 2. ----------
tree({'userChrome.css': '@import url("a.css");\n@import url("missing.css");\n',
      'userContent.css': '/* empty */\n',
      'a.css': 'x { color: a; }\n'},
     sections(cat('a.css'), cat('userContent.css')))
with contextlib.redirect_stdout(io.StringIO()) as out:
    code = mod.main([])
check('chain drift exits 2', code, 2)
check('chain drift names the offending import',
      json.loads(out.getvalue())['assertions'][0]['importsWithoutWrapMember'], ['missing.css'])

# --- Case 7: absent user config.css backed by the generated config is fine. --
tree({'userChrome.css': '@import url("a.css");\n@import url("config.css");\n',
      'userContent.css': '/* empty */\n',
      'a.css': 'x { color: a; }\n'},
     sections(cat('a.css') + '\n      cat "$configCssPath" >> "$out/x.css"', cat('userContent.css')))
with contextlib.redirect_stdout(io.StringIO()):
    code = mod.main([])
check('absent user config.css with generated configCss is not drift', code, 0)

print()
if failures:
    print(f'{len(failures)} fixture failure(s): ' + ', '.join(failures))
    sys.exit(1)
print('all cascade fixtures hold')
