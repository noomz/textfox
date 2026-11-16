#!/usr/bin/env python3
"""Check expansion of nested CSS selectors for bounded DOM inventory."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('expanded_selectors', HERE / 'expand-rule-selectors.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def record(selector, context):
    return {'selector':selector, 'context':context}

assert module.expand(record('> label, > image', ['#OtherBookmarks'])) == ':is(#OtherBookmarks) > label, :is(#OtherBookmarks) > image'
assert module.expand(record(':root[lwtheme] &', ['sidebar-main'])) == ':root[lwtheme] :is(sidebar-main)'
assert module.expand(record('&:hover, &:focus', ['@media (test)', '#nav-bar'])) == ':is(#nav-bar):hover, :is(#nav-bar):focus'
assert module.expand(record('[data-test="a,b"]', ['#parent'])) == ':is(#parent) [data-test="a,b"]'
assert module.expand(record('& > .child', ['#root', '&:hover'])) == ':is(:is(#root):hover) > .child'
for selector in ('&[data-test="&"]', r'& .foo\&bar'):
    try:
        module.expand(record(selector, ['#root']))
    except ValueError:
        pass
    else:
        raise AssertionError(f'unsafe ampersand accepted: {selector}')
print('Expanded selector fixture passed')
