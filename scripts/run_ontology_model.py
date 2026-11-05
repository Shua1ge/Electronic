"""Offline ontology model extraction: six new specifications, then related drawings.

Only discovery OCR/images and the ontology are read. No modeling examples, audit
answers or hand-filled facts are model inputs. Context and blind controls remain
separate; source figures are selected by drawing titles, not manually cropped.
"""
import argparse,hashlib,json,re,time
from pathlib import Path
from PIL import Image
from full_pdf import ROOT,save,sha256
from engineering_ontology import CATALOG
from ontology_extraction import paragraph_units,extraction_task
from ontology_model_io import entity_schema,facts_schema,stage_prompt,to_bundle
from evidence_core import decode

PROJECTS={'sample-e':'sample-e','sample-f':'sample-f','sample-g':'sample-g','sample-h':'sample-h','sample-i':'sample-i','单文件工程':'ict'}
SOURCES=['run_ontology_model.py','ontology_model_io.py','engineering_ontology.py','ontology_extraction.py']
SAMPLING={'temperature':0.7,'top_p':0.8,'top_k':20,'min_p':0.,'presence_penalty':1.5,'presence_context_size':32768,'repetition_penalty':1.}

def batches(page,max_chars=600):
    result=[];batch=[];size=0
    for unit in paragraph_units(page['lines']):
        if batch and (size+len(unit['text'])>max_chars or len(batch)>=5):result.append(batch);batch=[];size=0
        batch.append(unit);size+=len(unit['text'])
    if batch:result.append(batch)
    return result


def task_plan(pages):
    notes=[];diagrams=[]
    for p in pages:
        project=PROJECTS[p['group']];file=Path(p['file']).name
        note='卷册说明' in file or (project=='ict' and 4<=p['page']<=7)
        if note:
            for i,units in enumerate(batches(p),1):
                refs={sid for u in units for sid in u['source_ids']};lines=[r for r in p['lines'] if r['id'] in refs]
                bbox=[min(u['bbox'][0] for u in units),min(u['bbox'][1] for u in units),max(u['bbox'][2] for u in units),max(u['bbox'][3] for u in units)]
                notes.append({'id':f'{project}-note-{p["page"]:03}-{i:02}','project':project,'phase':'notes','source':p,'lines':lines,'bbox':bbox,'units':[u['id'] for u in units]})
        drawing=bool(re.search(r'系统通信方案图|业务通道组织图|纤芯说明表',file) or (project=='sample-g' and '光缆路由图' in file) or (project=='ict' and p['page'] in [8,12,19]))
        if drawing:
            key=p['document_sha256'][:8]+f'-{p["page"]:03}'
            task={'id':f'{project}-drawing-{key}','project':project,'phase':'drawings','source':p,'lines':p['lines'],'bbox':[0.,0.,1.,1.],'units':['whole'],'context_enabled':True}
            diagrams.append(task)
            # A paired no-context control on the same page and random seed.
            if '业务通道组织图' in file or (project=='ict' and p['page']==8):diagrams.append({**task,'id':task['id']+'-blind','context_enabled':False})
    return notes+diagrams


def relevant_context(candidates,lines,limit=20):
    text=' '.join(r['text'] for r in lines);terms=set(re.findall(r'[\u4e00-\u9fff]{1,8}(?:变|站|地调|备调)|[A-Z][A-Za-z0-9-]{3,}',text))
    scored=[]
    for c in candidates:
        snippet=c['subject']+' '+str(c['value']);score=sum(t in snippet for t in terms)
        if score:scored.append((score,c))
    scored.sort(key=lambda item:item[0],reverse=True)
    return [c for _,c in scored[:limit]]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--phase',choices=['notes','drawings','all'],default='all');parser.add_argument('--limit',type=int)
    parser.add_argument('--max-tokens',type=int,default=4096);parser.add_argument('--project',action='append')
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    pages=json.loads((ROOT/'outputs/ontology_discovery/new-pages.json').read_text());tasks=task_plan(pages)
    cfg=json.loads((ROOT/'config/model.json').read_text());code={s:sha256(ROOT/'scripts'/s) for s in SOURCES};code['ontology']=sha256(ROOT/'config/engineering-ontology.json')
    signature={'code':code,'model':cfg,'max_tokens':args.max_tokens,'sampling':SAMPLING,
               'sources':{p['file']:p['document_sha256'] for p in pages}}
    mf=out/'run.json'
    if mf.exists():
        old=json.loads(mf.read_text())
        if old['signature']!=signature:raise ValueError('Source/config/code changed: use a new output directory')
    state={'signature':signature,'status':'running','accuracy_status':'development_regression_not_independent','calls':[]}
    if mf.exists():state=json.loads(mf.read_text())
    else:
        for name in SOURCES:target=out/'source-code'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/'scripts'/name).read_bytes())
        save(out/'source-code/engineering-ontology.json',CATALOG)
    # Plan carries input references only, no answers.
    save(out/'plan.json',[{k:v for k,v in t.items() if k not in ['source','lines']}|{'file':t['source']['file'],'page':t['source']['page']} for t in tasks]);save(mf,state)
    tasks=[t for t in tasks if (args.phase=='all' or t['phase']==args.phase) and (not args.project or t['project'] in args.project)]
    if args.limit:tasks=tasks[:args.limit]
    import mlx.core as mx
    from mlx_vlm import load,generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    from mlx_vlm.structured import build_json_schema_logits_processor
    model_path=ROOT/cfg['local_directory'];model,processor=load(str(model_path));model_cfg=load_config(str(model_path))
    types=[t for t in CATALOG['classes'] if t not in ['EngineeringObject','DrawingOccurrence']]
    for number,task in enumerate(tasks,1):
        if any(sha256(ROOT/'scripts'/s)!=code[s] for s in SOURCES):raise ValueError('Code changed during run')
        folder=out/'tasks'/task['id'];folder.mkdir(parents=True,exist_ok=True);page=task['source'];context=[]
        if task['phase']=='drawings':
            expected=[t for t in task_plan(pages) if t['phase']=='notes' and t['project']==task['project']]
            if not all((out/'tasks'/t['id']/'result.json').exists() for t in expected):raise ValueError('Finish all specification tasks for this project before drawing checks')
            candidates=[]
            for t in expected:candidates.extend(json.loads((out/'tasks'/t['id']/'result.json').read_text()).get('context_candidates',[]))
            if task.get('context_enabled'):context=relevant_context(candidates,task['lines'])
        if task['phase']=='notes':
            unit={'text':'\n'.join(r['text'] for r in task['lines']),'bbox':task['bbox'],'source_ids':[r['id'] for r in task['lines']]}
            local_types=list(extraction_task(page,unit)['allowed_types'])
            # Include devices and ports for any passage describing their model or interface.
            if any(k in unit['text'] for k in ['设备','主机','接口','保护']):local_types=list(set(local_types)|{'Device','Port','Board','Slot','QuantityExpectation'})
        else:local_types=types
        local_types=[t for t in local_types if t!='DrawingOccurrence']
        prompt=stage_prompt(task['lines'],local_types,'entities',context=context,diagram=task['phase']=='drawings');schema=entity_schema(task['lines'],local_types)
        source=Image.open(page['image']).convert('RGB');overview=source.copy();overview.thumbnail((2048,2048));overview.save(folder/'overview.png');images=[str(folder/'overview.png')]
        if task['phase']=='notes':
            b=task['bbox'];b=[max(0,b[0]-.012),max(0,b[1]-.012),min(1,b[2]+.012),min(1,b[3]+.012)];w,h=source.size
            crop=source.crop(tuple(round(v*s) for v,s in zip(b,(w,h,w,h))));crop.thumbnail((3072,3072));crop.save(folder/'detail.png');images=[str(folder/'detail.png')]
        else:
            # Automated overlapping quadrants preserve small labels; no manually
            # chosen channels or expected endpoint annotations are supplied.
            w,h=source.size
            for i,b in enumerate([(0,0,.55,.55),(.45,0,1,.55),(0,.45,.55,1),(.45,.45,1,1)]):
                crop=source.crop(tuple(round(v*s) for v,s in zip(b,(w,h,w,h))));crop.thumbnail((2048,2048));crop.save(folder/f'detail-{i}.png');images.append(str(folder/f'detail-{i}.png'))
        fingerprint=hashlib.sha256((prompt+json.dumps(schema)+json.dumps(signature,sort_keys=True)).encode()).hexdigest()
        save(folder/'input.json',{'file':page['file'],'page':page['page'],'bbox':task['bbox'],'line_ids':[l['id'] for l in task['lines']], 'context':context,'images':images,'fingerprint':fingerprint})
        (folder/'prompt.txt').write_text(prompt);save(folder/'schema.json',schema)
        result_file=folder/'result.json'
        if result_file.exists():
            result=json.loads(result_file.read_text())
            if result['fingerprint']!=fingerprint:raise ValueError('Task input changed')
            print('Resume '+task['id'],flush=True);continue
        print(f"{number}/{len(tasks)} {task['id']} lines={len(task['lines'])} context={len(context)}",flush=True)
        attempts=[];bundle=None;validation=None;ctx=[];parsed=None
        for attempt in range(2):
            rawfile=folder/f'call-{attempt}.json';limit=args.max_tokens*(attempt+1)
            if rawfile.exists():response=json.loads(rawfile.read_text())
            else:
                mx.random.seed(0);start=time.perf_counter();formatted=apply_chat_template(processor,model_cfg,prompt,num_images=len(images),enable_thinking=False)
                answer=generate(model,processor,formatted,images,max_tokens=limit,verbose=False,logits_processors=[build_json_schema_logits_processor(processor.tokenizer,schema)],**SAMPLING)
                response={'raw':answer.text,'seconds':time.perf_counter()-start,'generation_tokens':answer.generation_tokens,'prompt_tokens':answer.prompt_tokens,
                          'token_limit':limit,'hit_token_limit':answer.generation_tokens>=limit,'mlx_peak_gb':mx.get_peak_memory()/1e9};save(rawfile,response);mx.clear_cache()
            attempts.append(response)
            try:
                if response['hit_token_limit']:raise ValueError('output truncated')
                parsed=decode(response['raw']);break
            except (ValueError,TypeError,KeyError) as error:validation={'valid':False,'issues':[{'code':'parse_failure','detail':str(error),'severity':'error'}]}
        if parsed is not None:
            fact_prompt=stage_prompt(task['lines'],[e['type'] for e in parsed['entities']],'facts',parsed['entities'],context,task['phase']=='drawings')
            fact_schema=facts_schema(task['lines'],parsed['entities']);(folder/'facts-prompt.txt').write_text(fact_prompt);save(folder/'facts-schema.json',fact_schema)
            facts=None
            if not parsed['entities']:facts={'attributes':[],'relations':[],'unresolved':[]}
            for attempt in range(2) if parsed['entities'] else []:
                rawfile=folder/f'facts-{attempt}.json';limit=args.max_tokens*(attempt+1)
                if rawfile.exists():response=json.loads(rawfile.read_text())
                else:
                    mx.random.seed(0);start=time.perf_counter();formatted=apply_chat_template(processor,model_cfg,fact_prompt,num_images=len(images),enable_thinking=False)
                    answer=generate(model,processor,formatted,images,max_tokens=limit,verbose=False,logits_processors=[build_json_schema_logits_processor(processor.tokenizer,fact_schema)],**SAMPLING)
                    response={'raw':answer.text,'seconds':time.perf_counter()-start,'generation_tokens':answer.generation_tokens,'prompt_tokens':answer.prompt_tokens,'token_limit':limit,'hit_token_limit':answer.generation_tokens>=limit,'mlx_peak_gb':mx.get_peak_memory()/1e9};save(rawfile,response);mx.clear_cache()
                attempts.append(response)
                try:
                    if response['hit_token_limit']:raise ValueError('facts output truncated')
                    facts=decode(response['raw']);break
                except (ValueError,TypeError) as error:validation={'valid':False,'issues':[{'code':'facts_parse_failure','detail':str(error),'severity':'error'}]}
            if facts is not None:
                combined={**facts,'entities':parsed['entities'],'unresolved':parsed.get('unresolved',[])+facts.get('unresolved',[])}
                bundle,validation,ctx=to_bundle(combined,page,task['lines'],task['bbox'],task['project'],task['id'])
        result={'id':task['id'],'project':task['project'],'phase':task['phase'],'file':page['file'],'page':page['page'],'fingerprint':fingerprint,
                'context_enabled':task.get('context_enabled',False),'context_count':len(context),'bundle':bundle,'validation':validation,'context_candidates':ctx,
                'seconds':sum(a['seconds'] for a in attempts),'attempts':len(attempts),'generation_tokens':sum(a['generation_tokens'] for a in attempts)}
        save(result_file,result);state['calls']=[c for c in state['calls'] if c['id']!=task['id']]+[{k:v for k,v in result.items() if k not in ['bundle','context_candidates']}];save(mf,state)
        print(f"  entities={len(bundle['entities']) if bundle else 0} statements={len(bundle['statements']) if bundle else 0} valid={validation['valid']} seconds={result['seconds']:.1f}",flush=True)
    state['status']='requested_phase_finished';state['last_phase']=args.phase;save(mf,state)

if __name__=='__main__':main()
