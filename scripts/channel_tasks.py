"""Per-channel VLM tasks with geometric endpoint constraints and coverage logs."""
import json
import re
import subprocess
import time
from pathlib import Path
from PIL import Image
from channel_geometry import trace_channels
from document_core import ROOT
from evidence_core import decode,explicit_field_normalizations,literal_supported,norm,validate_records
from evidence_pdf import extraction_prompt,output_schema
from full_pdf import save
from project_context import context_prompt


def caption_alternatives(geometry,assets,destination):
    """Read the actual node-caption pixels again; retain both OCR readings."""
    dest=Path(destination)/'captions';dest.mkdir(parents=True,exist_ok=True)
    source=Image.open(Path(assets['overview']).parents[1]/'assets'/Path(assets['overview']).stem/'ocr.png');w,h=source.size
    indexed={r['id']:r for r in assets['lines']};alternatives={}
    ids={i for c in geometry['channels'] for i in c['a_headers']+c['z_headers']+c['intermediate_headers']}
    for sid in sorted(ids):
        r=indexed[sid];b=r['bbox'];box=[max(0,b[0]-.008),max(0,b[1]-.006),min(1,b[2]+.008),min(1,b[3]+.006)]
        image=source.crop(tuple(round(v*s) for v,s in zip(box,(w,h,w,h))));image=image.resize((image.width*2,image.height*2))
        path=dest/f'{sid}.png';image.save(path);result=dest/f'{sid}.json'
        if not result.exists():subprocess.run([str(ROOT/'tools/bin/vision-ocr'),str(path),str(result)],check=True,timeout=120)
        read=json.loads(result.read_text());text=' '.join(x['text'] for x in read['lines'])
        if text and norm(text)!=norm(r['text']):
            alternatives[sid]={'id':'V_'+sid,'text':text,'bbox':b,'engine':'local_caption_reread','original_ocr_id':sid}
    for c in geometry['channels']:
        for role in ['a_headers','z_headers','intermediate_headers']:
            for sid in c[role].copy():
                if sid in alternatives:
                    c[role].append(alternatives[sid]['id']);c['lines'].append(alternatives[sid])
        c['lines']=list({l['id']:l for l in c['lines']}.values())
    save(Path(destination)/'caption-alternatives.json',alternatives)
    save(Path(destination)/'geometry.json',geometry)


def station_part(text):
    s=re.split(r'[（(]',text)[0].strip()
    if re.search(r'变|站|调|汇聚|交换机|路由器',s) and not re.search(r'线路保护|接入通道|数字通道',s):return s
    if re.fullmatch(r'[\u4e00-\u9fff]{2,8}',s) and not re.search(r'网|保护|通道|说明|光缆|光纤|设备|专用',s):return s
    return None


def constrain_endpoints(candidate,channel):
    """Only replace a contradictory endpoint when caption OCR agrees uniquely."""
    candidate={**candidate,'fields':dict(candidate.get('fields',{})), 'source_ids':list(candidate.get('source_ids',[])),
               'uncertainties':list(candidate.get('uncertainties',[]))};fixes=[]
    indexed={l['id']:l for l in channel['lines']}
    row=[indexed[i] for i in channel['row_ids']]
    explicit_pairs=[]
    for line in row:
        match=re.match(r'^([^：:至]{1,24}(?:变|站))至([^：:至]{1,24}(?:变|站))(?:线路保护)?[：:]',line['text'])
        if match:explicit_pairs.append((match[1],match[2],line['id']))
    def one_character_apart(a,b):
        return len(a)==len(b) and sum(x!=y for x,y in zip(a,b))<=1
    roles=[('A端站点','a_headers'),('Z端站点','z_headers')]
    # Preserve a model's explicit reversed A/Z orientation; screen-left is not A.
    left={norm(station_part(indexed[i]['text']) or indexed[i]['text']) for i in channel['a_headers']}
    right={norm(station_part(indexed[i]['text']) or indexed[i]['text']) for i in channel['z_headers']}
    if norm(candidate['fields'].get('A端站点')) in right and norm(candidate['fields'].get('Z端站点')) in left:
        roles=[('A端站点','z_headers'),('Z端站点','a_headers')]
    for field,role in roles:
        names={norm(name):name for sid in channel[role] if (name:=station_part(indexed[sid]['text']))}
        # A line's explicit A-to-Z label can corroborate a one-character OCR
        # disagreement in its own node caption. It is not a station dictionary.
        slot=0 if role=='a_headers' else 1
        corroborated={norm(pair[slot]):pair[slot] for pair in explicit_pairs
                      if any(one_character_apart(norm(pair[slot]),name) for name in names)}
        if len(corroborated)==1:
            names=corroborated
            candidate['source_ids'].extend(pair[2] for pair in explicit_pairs)
        old=candidate['fields'].get(field);short=explicit_field_normalizations('非保护业务',{field:old},'').get(field,old)
        if len(names)==1 and norm(short) not in names:
            value=next(iter(names.values()));candidate['fields'][field]=value
            fixes.append({'field':field,'model_value':old,'value':value,'reason':'line ends inside this node; unique caption OCR','source_ids':channel[role]})
        # Model chooses between conflicting OCR readings from the same node; do
        # not force the first reading, and never substitute a transit node.
        candidate.setdefault('source_ids',[]).extend(channel[role])
    row_text='\n'.join(r['text'] for r in row)
    values={}
    labels=set(re.findall(r'(?:调度|行政)IAD|IP电话|测距',row_text,re.I))
    if len(labels)==1 and channel['category']=='非保护业务':
        label=next(iter(labels));values['业务类型']=label;values['业务名称']=label
    rate_text='\n'.join(r['text'] for r in row if not re.search(r'[MG]\s*转',r['text']))
    rates=set(re.findall(r'(?<![A-Za-z0-9.])(\d+(?:\.\d+)?(?:[×xX]\d+)?[MG])(?:\s*(?:数字|业务)?通道)?',rate_text))
    if not rates and re.search(r'[A-Za-z]\d+(?:\.\d+)?[MG]',rate_text):
        candidate['uncertainties'].append('行内速率标记含字母/数字混淆，未从标记内部截取数值补入；需复核原图。')
    if len(rates)==1:values['申请业务速率']=next(iter(rates))
    if channel['category']=='保护业务':
        models=set(re.findall(r'(?<![A-Za-z0-9])[A-Z]{2,6}-\d[A-Za-z0-9]*(?:-[A-Za-z0-9]+)+',row_text))
        if len(models)==1:values['保护装置型号']=next(iter(models))
    system_headers=[]
    for sid in channel['a_headers']:
        match=re.search(r'[（(]([^（）()]*网[^（）()]*)[）)]',indexed[sid]['text'])
        if match:system_headers.append((match[1],sid))
    if len({norm(value) for value,_ in system_headers})==1:
        old_system=str(candidate['fields'].get('传输系统') or '')
        selected=next((s for s in system_headers if norm(old_system)==norm(s[0])),system_headers[0])
        values['传输系统']=selected[0]
    legend_rates={e['rate'] for e in channel.get('legend_evidence',[]) if e.get('rate')}
    if len(legend_rates)==1 and not rates:
        values['申请业务速率']=next(iter(legend_rates))
        candidate['source_ids'].extend(e['source_id'] for e in channel['legend_evidence'])
    elif rates and legend_rates and rates!=legend_rates:
        candidate['uncertainties'].append('行内速率与本页图例速率不同，保留行内读数并安排复查。')
    for field,value in values.items():
        old=candidate['fields'].get(field)
        if norm(old)!=norm(value):
            candidate['fields'][field]=value
            fixes.append({'field':field,'model_value':old,'value':value,'reason':'unique literal annotation on this traced channel or its A-node network caption',
                          'source_ids':channel['row_ids']+channel['a_headers']+[e['source_id'] for e in channel.get('legend_evidence',[])]})
    candidate['source_ids'].extend(channel['row_ids'])
    candidate['source_ids']=list(dict.fromkeys(candidate['source_ids']))
    return candidate,fixes


def channel_prompt(category,c):
    prompt=extraction_prompt(category,c['lines'])
    anchors={k:c[k] for k in ['id','line','a_headers','z_headers','intermediate_headers','row_ids']}
    anchors.update(path=c.get('path'),legend_evidence=c.get('legend_evidence',[]))
    return prompt+'\n本次只识别程序检测到的这一条连线，最多输出一条记录。第一张完整图提供背景，第二张含端点标题，第三张仅显示目标连线所在行，不提取邻行。'+\
        '\n几何定位（不是人工答案）：'+json.dumps(anchors,ensure_ascii=False)+\
        '\nA/Z必须是这条路径真正两端所在设备框的站点，屏幕左右不决定业务A/Z方向；穿过的框只算中途候选，不可当终点。图中同一框有多个OCR读法时以图像核对。'+\
        '\n请完整读取此行的业务名称、装置完整型号、速率和传输系统。没有线路保护证据时不要将普通数字通道改成保护业务。'+\
        '\n临时通道或长路由的中途通道资源使用情况，沿目标连线从左到右逐站记录；中间站名不可只写成2M转。'


def extract_geometry(page,assets,out,model,processor,model_cfg,sampling):
    import mlx.core as mx
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.structured import build_json_schema_logits_processor
    n=page['page'];dest=out/'assets'/f'{n:03d}'/'channels';geometry=trace_channels(assets,dest)
    if not geometry['channels']:return None
    caption_alternatives(geometry,assets,dest)
    records=[];rejected=[];statuses=[];unresolved=[];coverage=[]
    for c in geometry['channels']:
        category=c['category']
        if category not in ['保护业务','非保护业务']:
            unresolved.append(f"{c['id']}业务类别证据不足或与图例冲突；不能默认归为非保护业务")
            coverage.append({'channel':c['id'],'category':None,'records':0,'source_ids':[l['id'] for l in c['lines']], 'state':'needs_review','field_changes':[]})
            statuses.append({'category':None,'channel':c['id'],'success':False})
            continue
        prompt=channel_prompt(category,c)+context_prompt(out,c['lines']);schema=output_schema(category,c['lines'])
        schema['properties']['records']['maxItems']=1
        path=out/'channel-calls'/f'{n:03d}'/f"{c['id']}.json";path.parent.mkdir(parents=True,exist_ok=True)
        print(f"Page {n}: channel {c['id']}/{len(geometry['channels'])} {category}",flush=True)
        cached=path.exists() and path.with_suffix('.prompt.txt').exists() and path.with_suffix('.prompt.txt').read_text()==prompt
        if cached:response=json.loads(path.read_text())
        else:
            if path.exists():save(out/'before-channel-refinement'/f'{n:03d}'/path.name,json.loads(path.read_text()))
            images=[assets['overview'],c['image'],c['row_image']]
            actual=apply_chat_template(processor,model_cfg,prompt,num_images=3,enable_thinking=False)
            constrained=build_json_schema_logits_processor(processor.tokenizer,schema)
            path.with_suffix('.prompt.txt').write_text(prompt)
            mx.random.seed(0);started=time.perf_counter()
            answer=generate(model,processor,actual,images,max_tokens=2048,verbose=False,logits_processors=[constrained],**sampling)
            response={'raw':answer.text,'seconds':time.perf_counter()-started,'generation_tokens':answer.generation_tokens,
                      'prompt_tokens':answer.prompt_tokens,'token_limit':2048,'hit_token_limit':answer.generation_tokens>=2048,
                      'mlx_run_peak_gb':mx.get_peak_memory()/1e9,'images':images,'task':category,'channel':c['id']}
            mx.clear_cache()
        parsed=None;fixes=[]
        try:
            obj=decode(response['raw'])
            if response['hit_token_limit']:raise ValueError('channel output reached token budget')
            for i,candidate in enumerate(obj.get('records',[])):
                obj['records'][i],changes=constrain_endpoints(candidate,c);fixes.extend(changes)
            parsed=validate_records(json.dumps(obj,ensure_ascii=False),category,c['lines'])
            response['validation_error']=None
        except (ValueError,TypeError) as e:response['validation_error']=str(e)
        save(path,response);page['attempts'].append(response)
        accepted=parsed['records'] if parsed else []
        for r in accepted:
            r['channel_id']=c['id'];r['geometry']= {k:c[k] for k in ['line','a_box','z_box','a_headers','z_headers','intermediate_headers']}
            r['geometry'].update({k:c[k] for k in ['path','path_kind','legend_evidence','category_conflict'] if k in c})
            r['geometry_field_changes']=fixes
        records.extend(accepted)
        if parsed:rejected.extend(parsed['rejected_records'])
        coverage.append({'channel':c['id'],'category':category,'records':len(accepted),'source_ids':[l['id'] for l in c['lines']],
                         'state':'candidate' if accepted else 'unresolved','field_changes':fixes})
        statuses.append({'category':category,'channel':c['id'],'success':parsed is not None})
        if not accepted:unresolved.append(f"检测到连线{c['id']}，未得到可核对业务记录")
    # Old channel IDs can refer to different geometry after a refresh. Keep
    # responses for skipped channels in an archive, never among active calls.
    active={t['channel'] for t in statuses if t['success']}
    for old in (out/'channel-calls'/f'{n:03d}').glob('C*.json'):
        if old.stem not in active:
            archive=out/'unused-channel-calls'/f'{n:03d}';archive.mkdir(parents=True,exist_ok=True)
            old.rename(archive/old.name)
            prompt_file=old.with_suffix('.prompt.txt')
            if prompt_file.exists():prompt_file.rename(archive/prompt_file.name)
    covered={sid for c in geometry['channels'] for sid in c['row_ids']}
    uncovered=[r['id'] for r in assets['lines'] if re.search(r'测距|[IT]P电话|IAD|接入通道|套保护',r['text']) and r['id'] not in covered and not any(r['id']==e['source_id'] for e in geometry.get('legend_entries',[]))]
    if uncovered:unresolved.append('未绑定连线的业务文字：'+','.join(uncovered))
    save(dest/'coverage.json',{'channels':coverage,'unbound_lines':geometry['unbound_lines'],'uncovered_business_labels':uncovered,
                             'not_accuracy':'coverage is a geometric/text inventory, not factual correctness'})
    return {'records':records,'rejected_records':rejected,'tasks':statuses,'unresolved':unresolved,
            'coverage':{'detected_channels':len(coverage),'with_records':sum(c['records']>0 for c in coverage),
                        'unbound_lines':len(geometry['unbound_lines']),'uncovered_business_labels':len(uncovered)}}
