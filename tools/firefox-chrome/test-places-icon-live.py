#!/usr/bin/env python3
"""Check escaped Places icon IDs and live candidate joins."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('places_icons', HERE / 'check-places-icon-live.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

rules = [
    {'context':['@media example', '#placesContext'], 'selector':'#placesContext_open\\:newtab, #placesContext_copy'},
    {'context':['@media example', '#other'], 'selector':'#unrelated'},
]
ids = module.candidate_ids(rules)
assert ids == {'placesContext_open:newtab', 'placesContext_copy'}, ids
sample = {'firefox':{'nova':True, 'iconPref':True, 'version':'fixture', 'build':'fixture'}, 'placesKind':'folder',
          'placesContext':{'items':[
              {'id':'placesContext_open:newtab', 'hidden':False, 'display':'block',
               'visibility':'visible', 'width':10, 'height':10, 'image':'none'},
              {'id':'placesContext_copy', 'hidden':True, 'display':'none',
               'visibility':'visible', 'width':0, 'height':0, 'image':'none'}]}}
joined = module.compare_live(ids, [sample])
assert joined[0]['candidateVisibleImages'] == {'placesContext_open:newtab':'none'}, joined
assert joined[0]['candidateAbsent'] == [], joined
assert joined[0]['kind'] == 'folder', joined
print('Places icon join fixture passed')
