"""Reapply evidence/column checks to saved model responses, without new inference."""
import json
import sys
from collections import Counter
from pathlib import Path
from evidence_core import safe_aggregate,validate_records
from full_pdf import save,sha256
from channel_tasks import constrain_endpoints
from evidence_core import decode

out=Path(sys.argv[1]);observations=[];changes=[];pages=[]
for p in sorted((out/'pages').glob('*.json')):
    page=json.loads(p.read_text());n=page['page'];assets=json.loads((out/'assets'/f'{n:03d}'/'ocr.json').read_text())
    old=out/'before-field-normalization'/p.name
    if not old.exists():save(old,page)
    records=[];rejected=[];route=page['route']
    if route and isinstance(route.get('project'),dict):
        parsed=validate_records(json.dumps({'records':[route['project']]},ensure_ascii=False),'项目',assets['lines'])
        records.extend(parsed['records']);rejected.extend(parsed['rejected_records'])
    for task in page.get('tasks',[]):
        if not task['success']:continue
        cat=task['category'];unit=task.get('region','whole');lines=assets['lines']
        if task.get('channel'):
            geometry=json.loads((out/'assets'/f'{n:03d}'/'channels'/'geometry.json').read_text())
            channel=next(c for c in geometry['channels'] if c['id']==task['channel'])
            response=json.loads((out/'channel-calls'/f'{n:03d}'/f"{task['channel']}.json").read_text())
            obj=decode(response['raw']);fixes=[]
            for i,candidate in enumerate(obj.get('records',[])):
                obj['records'][i],changes_=constrain_endpoints(candidate,channel);fixes.extend(changes_)
            parsed=validate_records(json.dumps(obj,ensure_ascii=False),cat,channel['lines'])
            for r in parsed['records']:
                r['channel_id']=channel['id'];r['geometry']={k:channel[k] for k in ['line','a_box','z_box','a_headers','z_headers','intermediate_headers']}
                r['geometry'].update({k:channel[k] for k in ['path','path_kind','legend_evidence','category_conflict'] if k in channel})
                r['geometry_field_changes']=fixes
            records.extend(parsed['records']);rejected.extend(parsed['rejected_records'])
            continue
        if unit.startswith('region-'):
            rs=json.loads((out/'assets'/f'{n:03d}'/'groups'/'regions.json').read_text())
            region=next(r for r in rs if unit==f"region-{r['id']}")
            b=region['bbox'];ids={l['id'] for l in region['lines']}
            # Also resolve text visible in the actual supplied crop but omitted
            # from its OCR-text list. No new model values are generated here.
            lines=[l for l in assets['lines'] if l['id'] in ids or
                   (b[0]<=(l['bbox'][0]+l['bbox'][2])/2<=b[2] and b[1]<=(l['bbox'][1]+l['bbox'][3])/2<=b[3])]
        elif route['kind']=='设计说明':
            section=out/'assets'/f'{n:03d}'/f'section-{cat}.json'
            if section.exists():
                ids=set(json.loads(section.read_text())['source_ids']);lines=[l for l in lines if l['id'] in ids]
        if task.get('source_ids'):
            ids=set(task['source_ids']);lines=[l for l in assets['lines'] if l['id'] in ids]
        candidates=list((out/'routing-calls'/f'{n:03d}'/unit).glob(f'{cat}-*.json'))
        if not candidates and unit!='whole':candidates=list((out/'calls'/f'{n:03d}'/unit).glob(f'{cat}-*.json'))
        if not candidates:candidates=list((out/'calls'/f'{n:03d}').glob(f'{cat}-*.json'))
        parsed=None
        for response_path in sorted(candidates,reverse=True):
            response=json.loads(response_path.read_text())
            if response['hit_token_limit']:continue
            try:parsed=validate_records(response['raw'],cat,lines)
            except (ValueError,TypeError):continue
            break
        if parsed is None:raise ValueError(f'Cannot revalidate previously successful task {n}/{cat}/{unit}')
        for row in parsed['records']:row['region']=unit
        records.extend(parsed['records']);rejected.extend(parsed['rejected_records'])
    if page['extraction'] is not None:page['extraction']['records']=records
    page['rejected_records']=rejected
    for i,row in enumerate(records,1):
        rid=f'P{n:03d}R{i:03d}'
        observations.append({'id':rid,'page':n,**row})
        if row.get('field_normalizations'):changes.append({'record_id':rid,'page':n,'mappings':row['field_normalizations']})
    save(p,page);pages.append(page)
for name in ['observations','structured']:
    prior=out/f'{name}-before-field-normalization.json'
    if not prior.exists():prior.write_bytes((out/f'{name}.json').read_bytes())
save(out/'observations.json',observations);combined=safe_aggregate(observations);save(out/'structured.json',combined)
save(out/'field-normalizations.json',changes)
run=json.loads((out/'run.json').read_text())
run.update(observations=len(observations),output_records=len(combined['records']),
           category_counts=dict(Counter(r['category'] for r in combined['records'])),
           withheld_fields=sum(len(r['withheld_fields']) for r in observations),
           explicit_column_mappings=sum(len(c['mappings']) for c in changes),
           supplemented_citations=sum('unique_context' in c['check'] for r in observations for c in r.get('field_evidence',{}).values()),
           evidence_revalidation_sha256=sha256(Path(__file__).with_name('evidence_core.py')),
           supplementary_evidence_scope='same supplied region image, exact unique OCR value; relationship remains unverified',
           automatic_rotations=[{'page':p['page'],'ccw':p.get('rotation_ccw',0)} for p in pages if p.get('rotation_ccw')])
save(out/'run.json',run)
(out/'evidence-core-final.py').write_bytes(Path(__file__).with_name('evidence_core.py').read_bytes())
print(json.dumps({k:run[k] for k in ['observations','output_records','withheld_fields','explicit_column_mappings','supplemented_citations','automatic_rotations']},ensure_ascii=False,indent=2))
