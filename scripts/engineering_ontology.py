"""Typed, evidence-bearing statements over a versioned engineering ontology.

Validation does not prove that a drawing was read correctly. It rejects category
and scope errors and produces review issues; it never repairs source facts.
"""
import json
from pathlib import Path
from typing import Any,Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

ROOT=Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'config/engineering-ontology.json').read_text())

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Scope(Strict):
    project:str=Field(min_length=1)
    work_package:str|None=None
    engineering_phase:str|None=None
    stage:Literal['existing','construction','target','unknown']='unknown'
    temporality:Literal['permanent','temporary','unknown']='unknown'
    document_revision:str|None=None
    state_time:str|None=None

class Evidence(Strict):
    id:str
    file:str
    document_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    page:int=Field(ge=1)
    bbox:list[float]=Field(min_length=4,max_length=4)
    coordinate_space:Literal['normalized_page','normalized_oriented_page']='normalized_page'
    source_ids:list[str]=Field(min_length=1)
    quote:str=Field(min_length=1)
    reading_method:Literal['native_text','ocr','visual_review','model']
    @model_validator(mode='after')
    def bounds(self):
        x,y,z,t=self.bbox
        if not all(0<=v<=1 for v in self.bbox) or x>=z or y>=t:raise ValueError('invalid evidence rectangle')
        return self

class Entity(Strict):
    id:str
    type:str
    label:str
    scope:Scope
    evidence_ids:list[str]=Field(min_length=1)
    granularity:Literal['instance_candidate','class_reference','group_reference','expression']='instance_candidate'
    # Verified keys require explicit adjudication; names/models are not such keys.
    verified_identity:dict[str,str]=Field(default_factory=dict)
    identity_verified_by:str|None=None
    @model_validator(mode='after')
    def verified_keys_have_owner(self):
        if self.verified_identity and not self.identity_verified_by:raise ValueError('verified identity keys require an explicit adjudicator')
        return self

class Statement(Strict):
    id:str
    subject:str
    predicate:str
    kind:Literal['attribute','relation']
    value:Any=None
    object:str|None=None
    unit:str|None=None
    qualifiers:dict[str,Any]=Field(default_factory=dict)
    scope:Scope
    mode:Literal['asserted_fact','requirement','expectation','derived']='asserted_fact'
    status:Literal['candidate','corroborated','conflicted','rejected','human_confirmed']='candidate'
    evidence_ids:list[str]=Field(min_length=1)
    premise_ids:list[str]=Field(default_factory=list)
    rule_id:str|None=None
    adjudicator:str|None=None
    @model_validator(mode='after')
    def shape(self):
        if self.kind=='relation' and (self.object is None or self.value is not None):raise ValueError('relation needs object reference, not value')
        if self.kind=='attribute' and (self.object is not None or self.value is None):raise ValueError('attribute needs value, not object')
        if self.mode=='derived' and (not self.premise_ids or not self.rule_id):raise ValueError('derived statement needs premises and rule')
        if self.status=='human_confirmed' and not self.adjudicator:raise ValueError('human confirmation needs adjudicator')
        return self

class Bundle(Strict):
    ontology_version:Literal['0.3.0']='0.3.0'
    producer:str
    evidence:list[Evidence]
    entities:list[Entity]
    statements:list[Statement]
    notes:list[str]=Field(default_factory=list)


def is_a(kind,parent):
    seen=set()
    while kind and kind not in seen:
        if kind==parent:return True
        seen.add(kind);kind=CATALOG['classes'].get(kind,{}).get('parent')
    return False


def validate_bundle(data):
    bundle=Bundle.model_validate(data);issues=[]
    def issue(code,subject,detail,severity='error'):issues.append({'code':code,'subject':subject,'detail':detail,'severity':severity})
    def indexed(items):
        result={}
        for item in items:
            if item.id in result:issue('duplicate_id',item.id,'duplicate within collection')
            result[item.id]=item
        return result
    evidence=indexed(bundle.evidence);entities=indexed(bundle.entities);statements=indexed(bundle.statements)
    for e in bundle.entities:
        if e.type not in CATALOG['classes'] or e.type=='EngineeringObject':issue('unknown_or_abstract_type',e.id,e.type)
        for ref in e.evidence_ids:
            if ref not in evidence:issue('missing_evidence',e.id,ref)
        if e.granularity=='instance_candidate' and e.type in ['EquipmentModel','InterfaceSpec','FunctionalRole']:
            issue('catalog_not_instance',e.id,'specification or role needs class_reference')
    pairs={'Port':['Device','Board'],'Board':['Device'],'Slot':['Device','Rack'],'Rack':['Room'],'FiberRange':['CableSegment']}
    for s in bundle.statements:
        e=entities.get(s.subject)
        if not e:issue('missing_subject',s.id,s.subject);continue
        for ref in s.evidence_ids:
            if ref not in evidence:issue('missing_evidence',s.id,ref)
        if s.scope.project!=e.scope.project:issue('cross_project_scope',s.id,'statement and subject belong to different projects')
        definitions=CATALOG['relations'] if s.kind=='relation' else CATALOG['attributes']
        definition=definitions.get(s.predicate)
        if not definition:issue('unknown_predicate',s.id,s.predicate);continue
        if not any(is_a(e.type,d) for d in definition['domains']):issue('wrong_attribute_owner' if s.kind=='attribute' else 'wrong_relation_domain',s.id,e.type)
        if s.kind=='relation':
            target=entities.get(s.object)
            if not target:issue('missing_object',s.id,s.object)
            else:
                if not any(is_a(target.type,d) for d in definition['ranges']):issue('wrong_relation_range',s.id,target.type)
                if target.scope.project!=s.scope.project:issue('cross_project_scope',s.id,'target not in statement project')
                if s.predicate=='part_of' and e.type in pairs and not any(is_a(target.type,d) for d in pairs[e.type]):issue('wrong_part_parent',s.id,f'{e.type} cannot belong to {target.type}')
                if s.subject==s.object:issue('self_relation',s.id,'self-loop needs separate explicit domain rule')
            if set(s.qualifiers)-set(definition['qualifiers']):issue('unknown_relation_qualifier',s.id,str(set(s.qualifiers)-set(definition['qualifiers'])))
        else:
            value_type=definition['value_type']
            if value_type=='number' and (isinstance(s.value,bool) or not isinstance(s.value,(int,float))):issue('wrong_value_type',s.id,'numeric property cannot hold an interface/model token or expression')
            if value_type=='number' and isinstance(s.value,(int,float)) and not isinstance(s.value,bool):
                import math
                if not math.isfinite(s.value) or s.value<0:issue('invalid_numeric_value',s.id,'nonnegative finite value required')
            if value_type=='string' and (not isinstance(s.value,str) or not s.value.strip()):issue('wrong_value_type',s.id,'nonempty literal string required')
            if value_type=='integer_set' and (not isinstance(s.value,list) or not s.value or any(type(v)!=int or v<1 for v in s.value) or len(s.value)!=len(set(s.value))):issue('wrong_value_type',s.id,'unique positive resource indices required')
            if value_type=='role_count_map' and (not isinstance(s.value,dict) or not s.value or any(type(v)!=int or v<0 for v in s.value.values())):issue('wrong_value_type',s.id,'explicit role-count map required')
            units=definition['units']
            if units and s.unit not in units:issue('wrong_or_missing_unit',s.id,str(s.unit))
            if not units and s.unit is not None:issue('unexpected_unit',s.id,str(s.unit))
            if s.predicate=='quantity':
                if s.qualifiers.get('count_basis') not in CATALOG['count_bases']:issue('quantity_basis_missing',s.id,'new_added, target_total, etc. must be explicit')
                if isinstance(s.value,bool) or not isinstance(s.value,(int,float)) or s.value<0:issue('invalid_quantity',s.id,'quantity needs a nonnegative number; keep expressions separately')
                if e.type=='QuantityExpectation' and s.mode!='expectation':issue('expectation_not_fact',s.id,'quantity expectation must not become confirmed instance count')
        if s.scope.stage=='unknown':issue('stage_unknown',s.id,'do not compare as same construction stage','review')
        for premise in s.premise_ids:
            if premise not in statements:issue('missing_premise',s.id,premise)
        if s.mode=='derived' and s.rule_id not in {r['id'] for r in CATALOG['rules']}:issue('unknown_derivation_rule',s.id,str(s.rule_id))
    # Detect indirect derivation cycles, not just self-reference.
    def visit(k,trail):
        if k in trail:return True
        return any(visit(v,trail|{k}) for v in statements[k].premise_ids if v in statements)
    for s in bundle.statements:
        if visit(s.id,set()):issue('cyclic_derivation',s.id,'evidence derivation cannot be circular')
    return {'valid':not any(i['severity']=='error' for i in issues),'issues':issues,'entities':len(entities),'statements':len(statements),'not_semantic_accuracy':True}


def compare_statements(a,b,identity_confirmed=False):
    """Three-way evidence comparison; unknown scope prevents a conflict claim."""
    a=Statement.model_validate(a);b=Statement.model_validate(b)
    if a.subject!=b.subject and not identity_confirmed:return {'decision':'insufficient','reason':'object identity not confirmed'}
    if a.predicate!=b.predicate or a.kind!=b.kind:return {'decision':'different_dimension','reason':'different properties are not conflicts'}
    if a.scope.stage=='unknown' or b.scope.stage=='unknown':return {'decision':'insufficient','reason':'stage unknown'}
    if a.scope.model_dump()!=b.scope.model_dump() or a.mode!=b.mode or a.qualifiers!=b.qualifiers:
        return {'decision':'different_scope','reason':'phase, stage, temporality, responsibility, revision, modality or quantity basis differs'}
    if a.scope.document_revision is None or a.scope.work_package is None:
        return {'decision':'insufficient','reason':'revision or responsibility not resolved'}
    if a.kind=='attribute' and CATALOG['attributes'].get(a.predicate,{}).get('comparison')!='scalar':return {'decision':'insufficient','reason':'alias or set-valued property; different values may coexist'}
    if a.kind=='relation':return {'decision':'supports' if a.object==b.object else 'insufficient','reason':'multiple targets may coexist; no closed-world cardinality assumption'}
    conversions={'Mbit/s':('rate',1e6),'Gbit/s':('rate',1e9),'kbit/s':('rate',1e3),'m':('length',1),'km':('length',1000)}
    def value(s):
        if s.unit in conversions and isinstance(s.value,(int,float)) and not isinstance(s.value,bool):
            family,factor=conversions[s.unit];return family,s.value*factor
        return s.unit,s.value
    return {'decision':'supports' if value(a)==value(b) else 'conflict','reason':'same verified object, scoped scalar property and modality'}


def judge_identity(a,b):
    a=Entity.model_validate(a);b=Entity.model_validate(b)
    if a.scope.project!=b.scope.project:return {'decision':'insufficient','reason':'cross-project correspondence needs explicit linking evidence'}
    if a.type!=b.type:
        return {'decision':'insufficient' if is_a(a.type,b.type) or is_a(b.type,a.type) else 'different','reason':'compare object level before names'}
    shared=set(a.verified_identity)&set(b.verified_identity)
    # Keys are explicit adjudicated identifiers, never labels/model numbers.
    trusted=shared&{'asset_id','cable_segment_id','device_scoped_port_id','document_occurrence_id'}
    if trusted:
        outcomes={a.verified_identity[k]==b.verified_identity[k] for k in trusted}
        if len(outcomes)==1:return {'decision':'same' if True in outcomes else 'different','reason':'scoped verified identifier'}
    return {'decision':'insufficient','reason':'names, model codes, role or physical proximity alone do not prove identity'}


def catalog_audit():
    errors=[]
    for key,c in CATALOG['classes'].items():
        if c['parent'] and c['parent'] not in CATALOG['classes']:errors.append(key)
    for group in ['relations','attributes']:
        for key,d in CATALOG[group].items():
            for kind in d['domains']+d.get('ranges',[]):
                if kind not in CATALOG['classes']:errors.append(key+':'+kind)
    return errors

if __name__=='__main__':
    import sys
    if len(sys.argv)>1:print(json.dumps(validate_bundle(json.loads(Path(sys.argv[1]).read_text())),ensure_ascii=False,indent=2))
    else:
        assert not catalog_audit(),catalog_audit()
        (ROOT/'config/engineering-statements.schema.json').write_text(json.dumps(Bundle.model_json_schema(),ensure_ascii=False,indent=2))
        print(json.dumps({'classes':len(CATALOG['classes']),'relations':len(CATALOG['relations']),'attributes':len(CATALOG['attributes']),'rules':len(CATALOG['rules'])}))
