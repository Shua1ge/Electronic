import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from engineering_ontology import validate_bundle,compare_statements,judge_identity,catalog_audit,Bundle

SCOPE={'project':'P','stage':'target','work_package':'own','document_revision':'A'}
EVIDENCE={'id':'e','file':'sample.pdf','document_sha256':'a'*64,'page':1,'bbox':[.1,.1,.9,.9],'source_ids':['T1'],'quote':'示例原文','reading_method':'visual_review'}
def entity(i,t,label='候选'):
 return {'id':i,'type':t,'label':label,'scope':dict(SCOPE),'evidence_ids':['e'],'granularity':'class_reference' if t in ['EquipmentModel','InterfaceSpec','FunctionalRole'] else 'instance_candidate'}
def statement(i,sub,pred,value,unit=None):
 return {'id':i,'subject':sub,'predicate':pred,'kind':'attribute','value':value,'unit':unit,'scope':dict(SCOPE),'evidence_ids':['e']}
def bundle(es,ss):return {'producer':'unit_test','evidence':[dict(EVIDENCE)],'entities':es,'statements':ss}

class OntologyTests(unittest.TestCase):
 def test_catalog_has_no_dangling_types(self):self.assertEqual(catalog_audit(),[])
 def test_device_model_cannot_be_an_instance(self):
  a=entity('model','EquipmentModel');a['granularity']='instance_candidate'
  self.assertFalse(validate_bundle(bundle([a],[]))['valid'])
 def test_network_rate_cannot_become_business_rate(self):
  r=validate_bundle(bundle([entity('c','Channel')],[statement('s','c','network_nominal_rate',10,'Gbit/s')]))
  self.assertIn('wrong_attribute_owner',[i['code'] for i in r['issues']])
 def test_FE_is_an_interface_not_numeric_bandwidth(self):
  r=validate_bundle(bundle([entity('c','Channel')],[statement('s','c','channel_rate',100,'FE')]))
  self.assertFalse(r['valid'])
 def test_security_zone_is_not_network_plane(self):
  s={'id':'s','kind':'relation','subject':'d','predicate':'participates_in','object':'z','scope':dict(SCOPE),'evidence_ids':['e']}
  self.assertFalse(validate_bundle(bundle([entity('d','Device'),entity('z','SecurityZone')],[s]))['valid'])
 def test_slot_number_cannot_become_core_count(self):
  self.assertFalse(validate_bundle(bundle([entity('s','Slot')],[statement('x','s','core_count',6,'芯')]))['valid'])
 def test_expectation_does_not_count_as_constructed_fact(self):
  s=statement('q','q','quantity',3,'台');s['qualifiers']={'count_basis':'new_added'}
  self.assertFalse(validate_bundle(bundle([entity('q','QuantityExpectation')],[s]))['valid'])
  s['mode']='expectation';self.assertTrue(validate_bundle(bundle([entity('q','QuantityExpectation')],[s]))['valid'])
 def test_same_model_same_label_does_not_merge_devices(self):
  self.assertEqual(judge_identity(entity('a','Device','MODEL-X'),entity('b','Device','MODEL-X'))['decision'],'insufficient')
 def test_new_quantity_vs_total_is_not_conflict(self):
  a=statement('a','q','quantity',3,'台');b=statement('b','q','quantity',4,'台')
  a['mode']=b['mode']='expectation';a['qualifiers']={'count_basis':'new_added'};b['qualifiers']={'count_basis':'target_total'}
  self.assertEqual(compare_statements(a,b)['decision'],'different_scope')
 def test_unknown_stage_is_not_false_conflict(self):
  a=statement('a','c','channel_rate',2,'Mbit/s');b=copy.deepcopy(a);b['value']=20;a['scope']['stage']='unknown'
  self.assertEqual(compare_statements(a,b)['decision'],'insufficient')
 def test_same_scope_scalar_conflict_detected(self):
  a=statement('a','c','channel_rate',2,'Mbit/s');b=copy.deepcopy(a);b['value']=20
  self.assertEqual(compare_statements(a,b)['decision'],'conflict')
 def test_equivalent_units_support(self):
  a=statement('a','c','circuit_rate',2500,'Mbit/s');b=copy.deepcopy(a);b['value']=2.5;b['unit']='Gbit/s'
  self.assertEqual(compare_statements(a,b)['decision'],'supports')
 def test_engineering_phase_and_temporary_are_separate(self):
  a=statement('a','c','circuit_rate',622,'Mbit/s');b=copy.deepcopy(a);a['scope']['engineering_phase']='一期';b['scope']['engineering_phase']='二期'
  self.assertEqual(compare_statements(a,b)['decision'],'different_scope')
 def test_aliases_not_scalar_conflicts(self):
  a=statement('a','site','label','项目G');b=copy.deepcopy(a);b['value']='项目H'
  self.assertEqual(compare_statements(a,b)['decision'],'insufficient')
 def test_multi_terminal_protection_not_forced_to_two_ends(self):
  es=[entity('c','Channel')]+[entity(x,'Site') for x in ['a','b','d']]
  ss=[{'id':x,'kind':'relation','subject':'c','predicate':'business_end','object':x,'scope':dict(SCOPE),'evidence_ids':['e']} for x in ['a','b','d']]
  self.assertTrue(validate_bundle(bundle(es,ss))['valid'])
 def test_fiber_must_belong_to_cable_not_slot(self):
  s={'id':'x','kind':'relation','subject':'f','predicate':'part_of','object':'s','scope':dict(SCOPE),'evidence_ids':['e']}
  self.assertFalse(validate_bundle(bundle([entity('f','FiberRange'),entity('s','Slot')],[s]))['valid'])
 def test_missing_source_is_error(self):
  a=entity('a','Device');a['evidence_ids']=['missing']
  self.assertFalse(validate_bundle(bundle([a],[]))['valid'])
 def test_requirement_not_equal_completed_work(self):
  a=statement('a','p','backup_duration',4,'h');b=copy.deepcopy(a);a['mode']='requirement'
  self.assertEqual(compare_statements(a,b)['decision'],'different_scope')
 def test_derived_statements_need_non_circular_premises(self):
  a=statement('a','q','quantity',3,'台');a.update(mode='derived',rule_id='R05',premise_ids=['b'],qualifiers={'count_basis':'existing'})
  b=copy.deepcopy(a);b.update(id='b',premise_ids=['a'])
  self.assertIn('cyclic_derivation',[i['code'] for i in validate_bundle(bundle([entity('q','MaterialLine')],[a,b]))['issues']])
 def test_human_confirmation_needs_attribution(self):
  a=statement('a','c','channel_rate',2,'Mbit/s');a['status']='human_confirmed'
  with self.assertRaises(ValueError):Bundle.model_validate(bundle([entity('c','Channel')],[a]))

if __name__=='__main__':unittest.main()

class ValueTypeTests(unittest.TestCase):
 def test_FE_string_cannot_hide_under_numeric_unit(self):
  self.assertFalse(validate_bundle(bundle([entity('c','Channel')],[statement('s','c','channel_rate','FE','Mbit/s')]))['valid'])
 def test_nan_and_negative_rates_rejected(self):
  for v in [float('nan'),-2]:self.assertFalse(validate_bundle(bundle([entity('c','Channel')],[statement('s','c','channel_rate',v,'Mbit/s')]))['valid'])
 def test_verified_identity_requires_attribution(self):
  a=entity('a','Device');a['verified_identity']={'asset_id':'1'}
  with self.assertRaises(ValueError):judge_identity(a,entity('b','Device'))
 def test_quantity_expression_not_silently_flattened(self):
  s=statement('s','q','quantity','2*2','台');s.update(mode='expectation',qualifiers={'count_basis':'new_added'})
  self.assertFalse(validate_bundle(bundle([entity('q','QuantityExpectation')],[s]))['valid'])
 def test_paragraphs_do_not_join_columns(self):
  from ontology_extraction import paragraph_units
  rows=[{'id':'a','text':'左列第一行','bbox':[.1,.1,.4,.12]}, {'id':'b','text':'左列续行','bbox':[.1,.125,.4,.15]}, {'id':'c','text':'右列开头','bbox':[.55,.1,.9,.12]}]
  self.assertEqual([g['source_ids'] for g in paragraph_units(rows)],[['a','b'],['c']])
 def test_task_sources_are_closed_and_unexecuted(self):
  from ontology_extraction import extraction_task
  p={'document_sha256':'a'*64,'file':'input.pdf','page':1}
  u={'id':'p','text':'某业务系统数据库主机','source_ids':['T1'],'bbox':[.1,.1,.4,.2]}
  t=extraction_task(p,u)
  self.assertIn('ict_security',t['profiles']);self.assertEqual(t['execution_status'],'prepared_not_model_executed')
