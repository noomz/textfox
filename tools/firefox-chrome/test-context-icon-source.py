#!/usr/bin/env python3
"""Check context icon source comparison against a minimal markup fixture."""
import importlib.util
import json
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('context_icon_source', HERE / 'check-context-icon-source.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as directory:
    source = Path(directory) / 'fixture.xhtml'
    source.write_text('<menuitem id="context-openlinkprivate"/><menuitem id="not-in-theme"/>')
    result = module.compare(source)
    assert result['found'] == ['context-openlinkprivate'], result
    assert 'context-back' in result['notInFile'], result
    assert 'not-in-theme' not in result['found'], result
    sample = {'firefox':{'nova':False, 'iconPref':True, 'version':'test', 'build':'fixture'},
              'item':{'menuItems':[{'id':'context-openlinkprivate', 'hidden':False, 'display':'flex',
                                    'visibility':'visible', 'width':20, 'height':20, 'image':'fixture'},
                                   {'id':'context-back', 'hidden':True, 'display':'none',
                                    'visibility':'visible', 'width':0, 'height':0, 'image':'hidden'}]}}
    live = Path(directory) / 'live.jsonl'
    live.write_text(json.dumps(sample) + '\n')
    profile = module.compare_live(result, live)[0]
    assert profile['candidateVisible'] == ['context-openlinkprivate'], profile
    assert profile['candidateVisibleImages'] == {'context-openlinkprivate':'fixture'}, profile
    assert 'context-back' in profile['candidatePresent'], profile
    assert 'context-copy' in profile['candidateAbsent'], profile
print('Context icon source fixture passed')
