#!/usr/bin/env python3
"""Test indexed-about coverage and redirect classification."""
import runpy
from pathlib import Path

module=runpy.run_path(str(Path(__file__).with_name('summarize-about-index.py')))
summarize=module['summarize']
meta={'browserNova':False,'newtabNova':False,'version':'159','build':'1'}
base={'firefox':meta,'themed':True}
state={'url':'about:about','ready':'complete','font':'monospace','controls':[],'shadowControls':[]}
rows=[{**base,'index':['about:about','about:rights']},
      {**base,'requested':'about:about','state':state,'error':None},
      {**base,'requested':'about:rights','state':{**state,'url':'https://example.invalid/','ready':'interactive'},
       'error':{'type':'UnknownException','message':'Reached error page: about:neterror'}}]
report=summarize(rows,[(False,False,True)])
assert report['pages']['about:about']['status']=='complete'
assert report['pages']['about:rights']['status']=='redirect-offline'
try:
    summarize(rows[:-1],[(False,False,True)])
except ValueError:
    pass
else:
    raise AssertionError('missing indexed page accepted')
print('About index summary tests passed')
