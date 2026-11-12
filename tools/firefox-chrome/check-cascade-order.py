#!/usr/bin/env python3
"""Compare cascade order between the manual and NixOS textfox delivery chains.

Manual chain: chrome/userChrome.css resolved depth-first, each @import's sheet
inserted at the position of that @import statement (CSS Cascade: an @import is
only valid before other rules, and its content is spliced in at that point, so
it precedes the importing sheet's own later rules). Same for chrome/userContent.css.

NixOS chain: the literal concatenation order in nix/pkgs/wrapTextfox.nix,
recognising `cat "$src/<file>"`, `sed ... "$src/icons.css"`, `cat "$configCssPath"`
and `cat "$extra*Path"` as chain members. In the generated file the userChrome
@import lines land after style rules, so Firefox ignores them: they are NOT
expanded, and each one is reported as a diagnostic.

Only identical selector strings are compared; different selector strings have
different specificity and are not modelled here. Winner = last !important
declaration, else last declaration in chain order.

Declarations inside a conditional group rule (@media, @supports, @-moz-document)
are reported separately: chain order cannot decide them, so they need a live check.

A hazard is a static precedence claim, never proof of painted output. Confirm in
real Firefox before changing CSS.

Exit codes: 0 no hazard, 1 hazard found, 2 broken input or unparseable chain.
Usage: check-cascade-order.py [--json] [--config-css PATH] [--extra-user-chrome PATH]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'chrome'
WRAP = ROOT / 'nix' / 'pkgs' / 'wrapTextfox.nix'

CONDITIONAL_AT_RULES = ('@media', '@supports', '@container', '@-moz-document',
                        '@document', '@layer')
SKIP_AT_RULES = ('@font-face', '@keyframes', '@-moz-keyframes', '@page',
                 '@property', '@counter-style', '@font-feature-values', '@scope')


def strip_comments(text):
    return re.sub(r'/\*.*?\*/', lambda m: ' ' * len(m.group(0)), text, flags=re.S)


def norm(text):
    return ' '.join(text.split())


def split_top(text, sep):
    """Split on sep at bracket depth 0, outside strings."""
    out, depth, quote, buf, i = [], 0, None, [], 0
    while i < len(text):
        c = text[i]
        if quote:
            buf.append(c)
            if c == '\\':
                buf.append(text[i + 1:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'':
            quote = c
            buf.append(c)
        elif c in '([{':
            depth += 1
            buf.append(c)
        elif c in ')]}':
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            out.append(''.join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    out.append(''.join(buf))
    return out


def match_block(text, brace):
    """Return (body, end_index) for the block opening at brace."""
    depth, j, n = 0, brace, len(text)
    while j < n:
        c = text[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[brace + 1:j], j
        elif c in '"\'':
            quote = c
            j += 1
            while j < n and text[j] != quote:
                j += 2 if text[j] == '\\' else 1
        j += 1
    return text[brace + 1:], n


def resolve_selector(parent, selector):
    """Resolve a nested selector against its parent, across comma lists on both sides.

    `a, b { &::before { … } }` must become `a::before, b::before`.
    """
    parts = [norm(p) for p in split_top(selector, ',') if norm(p)]
    if not parent:
        return ', '.join(parts) if parts else norm(selector)
    parents = [norm(p) for p in split_top(parent, ',') if norm(p)] or ['']
    if not parts:
        return norm(selector)
    out = []
    for part in parts:
        if '&' in part:
            out.extend(norm(part.replace('&', p)) for p in parents)
        else:
            out.extend(norm(f'{p} {part}') for p in parents)
    return ', '.join(out)


def merge_condition(condition, prelude):
    if not condition:
        return norm(prelude)
    return f'{condition} ; {norm(prelude)}'


def walk_sheet(text, sheet, emit, diagnostics, parent=None, condition='', depth=0,
               imports_allowed=True):
    """Walk one sheet body, emitting declarations in source order.

    `emit` is a list of records with a caller-assigned order; this function
    appends, so the caller keeps one monotone counter across the whole chain.
    """
    i, n = 0, len(text)
    seen_rule = False
    while i < n:
        brace = text.find('{', i)
        semi = text.find(';', i)
        if brace == -1 and semi == -1:
            record(norm(text[i:]), sheet, emit, diagnostics, parent, condition, seen_rule,
                   imports_allowed)
            return seen_rule
        if brace != -1 and (semi == -1 or brace < semi):
            prelude = norm(text[i:brace])
            body, end = match_block(text, brace)
            if prelude.startswith('@'):
                at = prelude.split('(')[0].split()[0].lower()
                if at in CONDITIONAL_AT_RULES:
                    seen_rule = walk_sheet(body, sheet, emit, diagnostics, parent,
                                           merge_condition(condition, prelude), depth,
                                           imports_allowed) or seen_rule
                elif at not in SKIP_AT_RULES:
                    seen_rule = walk_sheet(body, sheet, emit, diagnostics, parent,
                                           condition, depth, imports_allowed) or seen_rule
            else:
                walk_sheet(body, sheet, emit, diagnostics,
                           resolve_selector(parent, prelude), condition, depth, imports_allowed)
                seen_rule = True
            i = end + 1
        elif semi != -1:
            seen_rule = record(norm(text[i:semi]), sheet, emit, diagnostics,
                               parent, condition, seen_rule, imports_allowed) or seen_rule
            i = semi + 1
        else:
            record(norm(text[i:]), sheet, emit, diagnostics, parent, condition, seen_rule,
                   imports_allowed)
            return seen_rule
    return seen_rule


def record(stmt, sheet, emit, diagnostics, parent, condition, seen_rule, imports_allowed=True):
    if not stmt:
        return seen_rule
    if stmt.startswith('@import'):
        if seen_rule or not imports_allowed:
            diagnostics.append({'sheet': sheet, 'kind': 'late-import-ignored', 'statement': stmt})
        else:
            diagnostics.append({'sheet': sheet, 'kind': 'import', 'statement': stmt})
        return seen_rule
    if stmt.startswith('@'):
        return seen_rule
    if parent is None:
        return seen_rule
    prop, sep, value = stmt.partition(':')
    if not sep:
        return seen_rule
    prop = norm(prop).lower()
    if not re.fullmatch(r'--[a-z0-9-]+|[a-z-]+', prop):
        return seen_rule
    emit.append({
        'sheet': sheet,
        'selector': parent,
        'property': prop,
        'condition': condition,
        'value': norm(value),
        'important': bool(re.search(r'!\s*important\b', value, re.I)),
    })
    return seen_rule


def import_targets(text):
    """@import targets in statement order, before any style rule is seen."""
    out, seen_rule = [], False
    text = strip_comments(text)
    i, n = 0, len(text)
    while i < n:
        brace = text.find('{', i)
        semi = text.find(';', i)
        if brace != -1 and (semi == -1 or brace < semi):
            _, end = match_block(text, brace)
            seen_rule = True
            i = end + 1
            continue
        if semi == -1:
            break
        stmt = norm(text[i:semi])
        if stmt.startswith('@import') and not seen_rule:
            m = re.search(r'url\(\s*["\']?([^"\')]+)', stmt)
            if m:
                out.append(m.group(1).strip())
        elif stmt.startswith('@charset') or stmt.startswith('@layer'):
            pass
        else:
            seen_rule = True
        i = semi + 1
    return out


def manual_chain(entry_name):
    """DFS chain for one manual entry sheet: imports spliced before own rules.

    A sheet's own @import statements must precede its rules, so splicing every
    import target ahead of that sheet's own declarations is exact for a valid
    sheet; a late @import is invalid and surfaces as a diagnostic instead.
    """
    chain, diagnostics, records = [], [], []
    seen = set()

    def resolve(name, base, depth):
        """`base` is the importing sheet's directory, so @import targets resolve
        relative to it, exactly as Firefox resolves url() in that sheet."""
        if depth > 8:
            raise RuntimeError(f'import recursion too deep at {name}')
        target = (base / name.split('/')[-1]) if '/' not in name else (CSS / name)
        label = target.relative_to(CSS).as_posix()
        if label in seen:
            diagnostics.append({'sheet': label, 'kind': 'duplicate-import-skipped'})
            return
        seen.add(label)
        chain.append(label)
        path = target
        if not path.exists():
            # config.css is user-owned and normally absent. An absent sheet is an
            # empty sheet, never a crash.
            diagnostics.append({'sheet': name, 'kind': 'missing-sheet-empty'})
            return
        text = strip_comments(path.read_text())
        for target in import_targets(text):
            resolve(target, path.parent, depth + 1)
        own = []
        walk_sheet(text, label, own, diagnostics)
        records.extend(own)

    resolve(entry_name, CSS, 0)
    for position, rec in enumerate(records):
        rec['position'] = position
    keyed = {}
    for rec in records:
        keyed.setdefault((rec['selector'], rec['property']), []).append(rec)
    return chain, keyed, diagnostics


def wrap_members():
    """Chain members of the generated userChrome.css and userContent.css."""
    text = WRAP.read_text()
    sections = {'userchrome': [], 'usercontent': []}
    current = None
    for line in text.splitlines():
        marker = re.match(r'\s*###\s*(\w+)', line)
        if marker:
            current = marker.group(1).strip().lower()
            continue
        if current not in sections:
            continue
        stmt = line.strip()
        if not (stmt.startswith('cat') or stmt.startswith('sed')):
            continue
        m = re.search(r'"\$src/([^"]+)"', stmt)
        if m:
            sections[current].append(('src', m.group(1)))
            continue
        m = re.search(r'cat\s+"\$(\w+)Path"', stmt)
        if m:
            sections[current].append(('generated', m.group(1)))
            continue
    return sections


def wrap_chain(members, config_css=None, extra_path=None):
    """Keyed records for one generated stylesheet, in literal cat order."""
    records, diagnostics = [], []
    for index, (kind, name) in enumerate(members):
        if kind == 'src':
            path = CSS / name
            if not path.exists():
                diagnostics.append({'sheet': name, 'kind': 'missing-member'})
                continue
            text = strip_comments(path.read_text())
            # In the generated file a member's own @import lines land after the
            # rules of earlier members, so Firefox ignores them.
            walk_sheet(text, name, records, diagnostics, imports_allowed=(index == 0))
        else:
            payload = None
            if name == 'configCss' and config_css:
                payload = Path(config_css).read_text()
            elif name == 'extraUserChrome' and extra_path:
                payload = Path(extra_path).read_text()
            if payload is None:
                diagnostics.append({'sheet': f'${name}', 'kind': 'member-not-supplied'})
                continue
            walk_sheet(strip_comments(payload), f'${name}', records, diagnostics)
    for position, rec in enumerate(records):
        rec['position'] = position
    keyed = {}
    for rec in records:
        keyed.setdefault((rec['selector'], rec['property']), []).append(rec)
    return keyed, diagnostics


def winner(decls):
    important = [d for d in decls if d['important']]
    return max(important or decls, key=lambda d: d['position'])


def compare(manual, wrap):
    hazards, conditional = [], []
    for key in sorted(set(manual) | set(wrap)):
        m, w = manual.get(key, []), wrap.get(key, [])
        if max(len(m), len(w)) < 2:
            continue
        sel, prop = key
        def shape(d):
            return (d['sheet'], d['condition'], d['value'], d['important'])
        if any(d['condition'] for d in m) or any(d['condition'] for d in w):
            if [shape(d) for d in m] == [shape(d) for d in w]:
                # Same conditional declarations in both chains: no precedence
                # question to raise, chain order cannot decide them either way.
                continue
            conditional.append({
                'selector': sel, 'property': prop,
                'manual': [f"{d['sheet']}{' [' + d['condition'] + ']' if d['condition'] else ''}" for d in m],
                'wrap': [f"{d['sheet']}{' [' + d['condition'] + ']' if d['condition'] else ''}" for d in w],
                'reason': 'conditional declarations; chain order cannot decide them',
            })
            continue
        if not m or not w or len(m) < 2 or len(w) < 2:
            continue
        mw, ww = winner(m), winner(w)
        shape = lambda d: (d['sheet'], d['value'], d['important'])
        if shape(mw) != shape(ww):
            hazards.append({
                'selector': sel, 'property': prop,
                'manualWinner': {'sheet': mw['sheet'], 'value': mw['value'], 'important': mw['important']},
                'wrapWinner': {'sheet': ww['sheet'], 'value': ww['value'], 'important': ww['important']},
                'manualChain': [d['sheet'] for d in m],
                'wrapChain': [d['sheet'] for d in w],
            })
    return hazards, conditional


def main(argv):
    config_css = extra = None
    if '--config-css' in argv:
        config_css = argv[argv.index('--config-css') + 1]
    if '--extra-user-chrome' in argv:
        extra = argv[argv.index('--extra-user-chrome') + 1]
    report = {'chains': {}, 'hazards': [], 'conditionalNeedsLiveCheck': [],
              'diagnostics': [], 'assertions': []}
    for entry, section in (('userChrome.css', 'userChrome'), ('userContent.css', 'userContent')):
        if not (CSS / entry).exists():
            print(f'no such entry sheet: {CSS / entry}', file=sys.stderr)
            return 2
        members = wrap_members()[section.lower()]
        manual_files, manual, mdiag = manual_chain(entry)
        wkeyed, wdiag = wrap_chain(members, config_css, extra)
        hazards, conditional = compare(manual, wkeyed)
        # Chain-set assertion: every manual @import target must appear exactly once
        # among the concatenated members. The one expected exception is config.css,
        # which is user-owned and excluded from the chain by design; the generated
        # config member stands in for it, and its absence is what breaks Nix users'
        # overrides silently.
        targets = [(CSS / t).relative_to(CSS).as_posix()
                   for t in import_targets((CSS / entry).read_text())]
        src_members = [n for k, n in members if k == 'src']
        generated = [n for k, n in members if k == 'generated']
        missing = [t for t in targets
                   if t not in src_members and not (t == 'config.css' and 'configCss' in generated)]
        extra_members = [n for n in src_members if n not in targets and n != entry]
        report['assertions'].append({
            'entry': entry,
            'importsWithoutWrapMember': missing,
            'wrapMembersNotImported': extra_members,
        })
        report['chains'][entry] = {'manual': manual_files,
                                   'wrap': [f'{k}:{n}' for k, n in members]}
        report['hazards'].extend(hazards)
        report['conditionalNeedsLiveCheck'].extend(conditional)
        for d in mdiag + wdiag:
            report['diagnostics'].append(dict(d, chain=entry))
    print(json.dumps(report, indent=2))
    broken = [a for a in report['assertions']
              if a['importsWithoutWrapMember'] or a['wrapMembersNotImported']]
    if broken:
        print('chain drift: manual imports and NixOS members disagree', file=sys.stderr)
        return 2
    return 1 if report['hazards'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # a broken harness must never look like a hazard
        print(f'check-cascade-order: internal error: {exc}', file=sys.stderr)
        sys.exit(2)
