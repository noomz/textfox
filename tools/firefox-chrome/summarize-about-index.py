#!/usr/bin/env python3
"""Validate and summarize the disposable Firefox about:about matrix JSONL."""
import argparse
from itertools import product
import json
from pathlib import Path


def summarize(rows, expected_profiles):
    indices={}
    pages={}
    versions=set()
    for row in rows:
        meta=row['firefox']
        key=(meta['browserNova'],meta['newtabNova'],row['themed'])
        versions.add((meta['version'],meta['build']))
        if 'index' in row:
            if key in indices:
                raise ValueError(f'duplicate index profile: {key}')
            indices[key]=row['index']
        elif 'requested' in row:
            page=row['requested']
            if (key,page) in pages:
                raise ValueError(f'duplicate page sample: {key}, {page}')
            pages[key,page]=row
        else:
            raise ValueError('unknown output row')
    if set(indices)!=set(expected_profiles) or len(versions)!=1:
        raise ValueError(f'profile matrix or Firefox version incomplete: {list(indices)}, {versions}')
    index=next(iter(indices.values()))
    if not index or len(index)!=len(set(index)) or any(value!=index for value in indices.values()):
        raise ValueError('empty, duplicated, or differing profile indexes')
    expected={(key,page) for key in expected_profiles for page in index}
    if set(pages)!=expected:
        raise ValueError(f'page matrix mismatch: missing={sorted(expected-set(pages))}, extra={sorted(set(pages)-expected)}')
    results={}
    for page in index:
        samples=[pages[key,page] for key in expected_profiles]
        complete=all(s['error'] is None and s['state'] and s['state']['ready']=='complete'
                     and s['state']['url'].split('#')[0]==page for s in samples)
        redirect=all(s['error'] and 'about:neterror' in s['error']['message'] and s['state']
                     and s['state']['url'].startswith(('http://','https://')) for s in samples)
        status='complete' if complete else 'redirect-offline' if redirect else 'inconclusive'
        themed=[s for s in samples if s['themed'] and s['state']]
        root_fonts=sorted({s['state']['font'] or '(empty)' for s in themed})
        visible_counts=[]
        differences=[]
        for sample in themed:
            controls=sample['state']['controls']+sample['state']['shadowControls']
            visible=[control for control in controls if control['visible']]
            visible_counts.append(len(visible))
            differences.extend(control for control in visible if control['font']!='monospace')
        results[page]={'status':status,'rootFonts':root_fonts,'visibleControls':visible_counts,
                       'differentVisibleControls':len(differences),
                       'destinations':sorted({s['state']['url'] for s in samples if s['state'] and s['state']['url']!=page})}
    version,build=next(iter(versions))
    return {'version':version,'build':build,'profiles':len(indices),'pages':results}


def render(report):
    lines=['# Indexed about-page inventory', '',
           f"Firefox {report['version']} build `{report['build']}`; {report['profiles']} disposable profiles covering independent browser/new-tab Nova preferences and vanilla/textfox.",
           'The full probe ran under a network namespace. Each indexed URL was navigated; controls were not activated. '
           'Complete means document ready state, not that asynchronous page content, feeds, or all controls initialized. '
           'Themed profiles configured the root font to `monospace`. Visible controls cover selected button/input-like hosts and one level of open shadow roots only; zero means none sampled. '
           'Paint, accessibility, populated data, parameterized URLs, closed shadow roots, and delivery paths remain unverified.', '',
           '| Indexed URL | Navigation | Themed root fonts | Visible themed controls (min–max) | Visible controls with another font |',
           '|---|---|---|---:|---:|']
    for page,result in report['pages'].items():
        counts=result['visibleControls']
        controls=f'{min(counts)}–{max(counts)}' if counts else 'not sampled'
        status=result['status']
        fonts=', '.join(result['rootFonts']) or 'not sampled'
        if status=='redirect-offline':
            status+=' → '+', '.join(result['destinations'])
            fonts='error document: '+fonts
        lines.append(f"| `{page}` | {status} | {fonts} | {controls} | {result['differentVisibleControls']} |")
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    args=parser.parse_args()
    rows=[json.loads(line) for line in args.input.read_text().splitlines() if line]
    report=summarize(rows,list(product((False,True),(False,True),(False,True))))
    print(render(report),end='')
    if any(page['status']=='inconclusive' for page in report['pages'].values()):
        raise SystemExit('inconclusive page navigation; inspect JSONL before accepting inventory')


if __name__=='__main__':
    main()
