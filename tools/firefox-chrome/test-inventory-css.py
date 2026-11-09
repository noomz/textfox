#!/usr/bin/env python3
"""Check CSS inventory output on nested and conditional rules."""
import importlib.util
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('inventory_css', HERE / 'inventory-css.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(dir=module.ROOT) as directory:
    path = Path(directory) / 'fixture.css'
    path.write_text('''/* --not-a-variable */
@media (width > 1px) {
  .outer {
    color: var(--real-color);
    content: "--not-a-variable";
    &:hover { border-color: var(--hover-color); }
  }
}
''')
    result = module.inventory(path)
    assert result['customProperties'] == ['--hover-color', '--real-color'], result
    assert result['rules'] == [
        {'line': 3, 'context': ['@media (width > 1px)'], 'selector': '.outer'},
        {'line': 6, 'context': ['@media (width > 1px)', '.outer'], 'selector': '&:hover'},
    ], result
print('CSS inventory fixture passed')
