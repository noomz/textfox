#!/usr/bin/env python3
"""Check declaration/reference inventory on nested CSS and var fallbacks."""
import importlib.util
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('variable_inventory', HERE / 'inventory-variables.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(dir=module.ROOT) as directory:
    path = Path(directory) / 'fixture.css'
    path.write_text('''/* var(--ignored) */
@media (width > 1px) {
  :root {
    --own: var(--other, var(--fallback, red));
    color: var(--own);
    content: "var(--ignored)";
    &:hover { border-color: var(--other); }
  }
}
''')
    result = module.inventory(path)
    assert set(result) == {'--own', '--other', '--fallback'}, result
    assert len(result['--own']['declarations']) == 1 and len(result['--own']['references']) == 1, result
    assert [x['property'] for x in result['--other']['references']] == ['--own', 'border-color'], result
    assert result['--fallback']['references'][0]['context'] == ['@media (width > 1px)', ':root'], result
    assert result['--other']['references'][1]['context'][-1] == '&:hover', result
    path.write_text('a { broken }')
    try:
        module.inventory(path)
    except ValueError:
        pass
    else:
        raise AssertionError('invalid CSS declaration did not fail inventory')
ledger = '## Custom-property syntax reconciliation\n`--one`, `--two`\n## Next\n`--ignored`'
module.check_ledger({'--one', '--two'}, ledger)
try:
    module.check_ledger({'--one'}, ledger)
except ValueError:
    pass
else:
    raise AssertionError('extra ledger variable was not detected')
print('CSS variable inventory fixture passed')
