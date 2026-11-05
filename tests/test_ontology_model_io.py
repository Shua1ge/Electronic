import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ontology_model_io import facts_schema,to_bundle

LINE={'id':'T1','text':'甲站光缆48芯，使用1、2芯','bbox':[.1,.1,.9,.2]}
PAGE={'file':'test.pdf','page':1,'document_sha256':'a'*64}
E={'id':'E1','type':'CableSegment','label':'光缆','scope':{'stage':'target','phase':None,'temporality':'unknown','responsibility':'unknown'},'refs':['T1']}

class ModelIOTests(unittest.TestCase):
 def test_core_count_schema_restricts_subject(self):
  es=[E,{**E,'id':'E2','type':'Network'}];s=facts_schema([LINE],es)
  core=next(x for x in s['properties']['attributes']['items']['anyOf'] if x['properties']['predicate']['const']=='core_count')
  self.assertEqual(core['properties']['subject']['enum'],['E1']);self.assertEqual(core['properties']['value']['type'],'number')
 def test_native_numeric_value_preserved_and_refs_bound(self):
  raw={'entities':[E],'attributes':[{'subject':'E1','predicate':'core_count','value':48,'unit':'芯','mode':'asserted_fact','count_basis':'unknown','refs':['T1']}],'relations':[],'unresolved':[]}
  b,v,c=to_bundle(raw,PAGE,[LINE],[.1,.1,.9,.2],'p','t')
  self.assertTrue(v['valid']);self.assertEqual(b['statements'][0]['value'],48);self.assertEqual(b['evidence'][0]['quote'],LINE['text']);self.assertTrue(c)
 def test_wrong_owner_is_not_offered_as_context(self):
  raw={'entities':[{**E,'type':'Network'}],'attributes':[{'subject':'E1','predicate':'core_count','value':48,'unit':'芯','mode':'asserted_fact','count_basis':'unknown','refs':['T1']}],'relations':[],'unresolved':[]}
  _,v,c=to_bundle(raw,PAGE,[LINE],[.1,.1,.9,.2],'p','t');self.assertFalse(v['valid']);self.assertEqual(c,[])
 def test_same_label_does_not_implicitly_add_relations(self):
  raw={'entities':[E,{**E,'id':'E2'}],'attributes':[],'relations':[],'unresolved':[]}
  b,v,c=to_bundle(raw,PAGE,[LINE],[.1,.1,.9,.2],'p','t');self.assertEqual(len(b['entities']),2);self.assertEqual(b['statements'],[])
