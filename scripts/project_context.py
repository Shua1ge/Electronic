"""Source-scoped claims and conservative identity decisions; never writes business facts."""
import hashlib
import json
import re
from itertools import combinations
from pathlib import Path
from engineering_ontology import CATALOG
from ontology_extraction import paragraph_units,extraction_task

TYPES = ['站点','业务机构','网络平面','设备实例','端口接口','物理光缆','纤芯资源','光路','业务通道','施工活动','图纸表达']
RELATIONS = {'安装于':['设备实例','站点'],'属于':['端口接口','设备实例'],
             '参与':['设备实例','网络平面'],'使用':['光路','纤芯资源'],
             '包含':['物理光缆','纤芯资源'],'承载':['光路','业务通道'],
             '表示':['图纸表达','工程对象'],'作用于':['施工活动','工程对象']}
RULES = {
 'identity':{'conditions':'类型、阶段、身份区分字段及来源范围均已确定',
             'support':'同一来源表达，或明确工程标识和完整区分条件',
             'forbidden':'仅按同名、端点、型号、速率合并；未知阶段默认相同',
             'counterexample':'同两端同速率的Ⅰ/Ⅱ回、不同保护套数、不同网络平面'},
 'quantity':{'conditions':'说明明确给出动作、数量与对象类型',
             'support':'原始段落及页内位置',
             'forbidden':'按期望数量创建已确认对象；未消歧前按记录数认定缺项',
             'counterexample':'两条物理路由承载一条光路；一个对象跨页重复出现'},
 'stage':{'conditions':'明确的现状、施工过渡或目标标记',
          'support':'原始陈述中的标记，不向全页传播',
          'forbidden':'同段出现多个阶段时强制选一个',
          'counterexample':'利用原有光缆新建临时业务通道'},
}

def save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2))

def stage_of(text):
    hits=[s for s,p in [('现状',r'原有|现有|已有'),('过渡',r'临时|过渡|施工期间'),('目标',r'新建|新增|扩容|建成后|投运后')] if re.search(p,text)]
    return hits[0] if len(hits)==1 else '未确定'

def source_ref(run,page,line):
    return {'document_sha256':run['signature']['pdf_sha256'],'document_version':'未确认',
            'page':page,'ocr_id':line['id'],'bbox':line['bbox'],'coordinate_space':'normalized_oriented_page',
            'text':line['text']}

def build_context(folder):
    folder=Path(folder);run=json.loads((folder/'run.json').read_text());claims=[];expected=[];plans=[]
    for path in sorted((folder/'pages').glob('*.json')):
        page=json.loads(path.read_text())
        if (page.get('route') or {}).get('kind')!='设计说明':continue
        assets=json.loads((folder/'assets'/path.stem/'ocr.json').read_text())
        source_page={'document_sha256':run['signature']['pdf_sha256'],'file':run.get('input','unknown'),'page':page['page']}
        plans.extend(extraction_task(source_page,unit) for unit in paragraph_units(assets['lines']))
        for line in assets['lines']:
            text=line['text']
            if not re.search(r'新建|新增|原有|现有|利旧|改造|改接|拆除|临时|过渡|投运|光缆|光路|保护|传输网',text):continue
            cid=f"N{page['page']:03d}-{line['id']}";ref=source_ref(run,page['page'],line)
            claims.append({'id':cid,'status':'待验证陈述','stage':stage_of(text),'source':ref,
                           'text':text,'actions':re.findall(r'新建|新增|利旧|改造|改接|拆除|临时',text)})
            m=re.search(r'(新建|新增)\s*([一二两三四五六七八九十\d]+)\s*(根|条)\s*(\d+)\s*芯\s*(OPGW|光缆)',text,re.I)
            if m:
                digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
                count=int(m[2]) if m[2].isdigit() else digits.get(m[2])
                if count:expected.append({'claim_id':cid,'type':'物理光缆','expected_count':count,'cores':int(m[4]),
                                          'status':'待核对，不能生成实体','source':ref})
    result={'schema_version':1,'ontology_version':CATALOG['version'],'typed_extraction_status':'task_plan_only_not_executed','document_sha256':run['signature']['pdf_sha256'],'claims':claims,'expectations':expected,
            'limits':['OCR原文陈述，不等同已确认事实','跨OCR行的数量/动作暂不自动合并','阶段不明确或混合时保留未确定']}
    save(folder/'engineering/ontology-task-plan.json',plans)
    save(folder/'engineering/context.json',result);return result

def context_prompt(folder,lines,limit=12):
    p=Path(folder)/'engineering/context.json'
    if not p.exists():return ''
    context=json.loads(p.read_text());text=' '.join(l['text'] for l in lines)
    tokens=set(re.findall(r'[\u4e00-\u9fff]{1,10}(?:变|站|地调|备调)|[A-Z]+[-\dA-Za-z]+',text))
    selected=[c for c in context['claims'] if any(t in c['text'] for t in tokens)][:limit]
    if not selected:return ''
    return '\n以下是施工说明的待验证陈述，仅用于提出核对问题，不是本图答案。不能据此填写本图未显示的端点、速率或数量；可能有OCR错误或阶段差异。引用仍只能使用本图OCR行。\n'+json.dumps(
        [{'id':c['id'],'page':c['source']['page'],'stage':c['stage'],'text':c['text']} for c in selected],ensure_ascii=False)

IDENTITY_KEYS={'物理光缆':['回次','线路编号'], '光路':['传输系统','光路编号'],
               '业务通道':['传输系统','线路回次','保护套数','通道编号'],
               '设备实例':['站点','网络平面','设备编号']}

def identity(a,b):
    if a['type']!=b['type']:return '不同对象','对象类型不同'
    if a.get('occurrence_id') and a.get('occurrence_id')==b.get('occurrence_id'):
        return '同一对象','同一图纸表达的重复引用（不证明跨页身份）'
    # Stage changes can describe one object's lifecycle; they are not distinct IDs.
    if a.get('stage','未确定')!=b.get('stage','未确定') or a.get('stage','未确定')=='未确定':
        return '证据不足','阶段未确定或不同，需要施工活动关系'
    fa,fb=a['attributes'],b['attributes']
    if any(fa.get(k) and fb.get(k) and fa[k]!=fb[k] for k in IDENTITY_KEYS.get(a['type'],[])):
        return '不同对象','同阶段的身份区分字段不一致'
    return '证据不足','同名、同端点或同型号不足以确认对象身份'

def build_graph(folder):
    folder=Path(folder);ctx=build_context(folder);run=json.loads((folder/'run.json').read_text())
    rows=json.loads((folder/'observations.json').read_text());objects=[];occurrences=[];claims=[];relations=[];tasks=[]
    mapping={'光缆':'物理光缆','光路':'光路','保护业务':'业务通道','非保护业务':'业务通道'}
    for row in rows:
        if row['category'] not in mapping:continue
        oid='O-'+row['id'];xid='X-'+row['id'];attrs=row['fields'];refs=row.get('field_evidence',{})
        # Stage is deliberately not inferred from unrelated OCR text on this page.
        stage=stage_of(str(attrs.get('申请说明') or '')+' '+str(attrs.get('名称') or '')+' '+str(attrs.get('申请有效期类型') or ''))
        objects.append({'id':oid,'type':mapping[row['category']],'stage':stage,'attributes':attrs,'occurrence_id':xid,'status':'候选对象'})
        occurrences.append({'id':xid,'type':'图纸表达','page':row['page'],'record_id':row['id'],
                            'document_sha256':ctx['document_sha256'],'document_version':'未确认','field_evidence':refs})
        relations.append({'type':'表示','from':xid,'to':oid,'status':'来源对应'})
        for k,v in attrs.items():
            if v is None:continue
            claims.append({'id':f'{row["id"]}:{k}','object':oid,'attribute':k,'value':v,'stage':stage,
                           'source':refs.get(k),'status':'图中候选，关系未确认'})
        for field in ['A端站点','Z端站点']:
            value=attrs.get(field)
            if not value:continue
            eid=oid+':'+field;kind='业务机构' if re.search(r'地调|备调|中调',str(value)) else '站点'
            objects.append({'id':eid,'type':kind,'stage':stage,'attributes':{'名称':value},'status':'端点名称候选'})
            relations.append({'type':'业务端点' if mapping[row['category']]=='业务通道' else '连接端点',
                              'from':oid,'to':eid,'status':'候选','source':refs.get(field),'role':field})
        if attrs.get('A端站点') and attrs.get('A端站点')==attrs.get('Z端站点'):
            tasks.append({'kind':'端点一致','object':oid,'page':row['page'],'source_record':row['id'],
                          'question':'两端被识别为同一站点；核对是否实际环回/站内连接，还是错把中途节点或同一框重复当终点','status':'待复查'})
        if not attrs.get('A端站点') or not attrs.get('Z端站点'):
            tasks.append({'kind':'缺端点','object':oid,'page':row['page'],'source_record':row['id'],
                          'question':'沿原图连续路径核对两个真正终点，不能用中途站补齐','status':'待复查'})
    decisions=[];primary=[o for o in objects if o.get('occurrence_id')]
    groups={}
    for o in primary:
        f=o['attributes'];key=(o['type'],f.get('A端站点'),f.get('Z端站点'))
        if key[1] and key[2]:groups.setdefault(key,[]).append(o)
    for group in groups.values():
        for a,b in combinations(group,2):
            result,reason=identity(a,b);decisions.append({'a':a['id'],'b':b['id'],'decision':result,'reason':reason,'rule':'identity'})
    for expectation in ctx['expectations']:
        tasks.append({'kind':'数量核对','page':expectation['source']['page'],'source':expectation['source'],
                      'expectation':expectation,'question':'定位对应路由图并区分回次与阶段后核对独立实体数量；当前不判定已漏识','status':'待复查'})
    for p in sorted((folder/'pages').glob('*.json')):
        page=json.loads(p.read_text());geometry=folder/'assets'/p.stem/'channels/geometry.json'
        if not geometry.exists():continue
        g=json.loads(geometry.read_text())
        for channel in g.get('channels',[]):
            if channel.get('category') is None:
                tasks.append({'kind':'图例与业务类别','page':page['page'],'geometry':{'path':channel.get('path'),'line':channel['line']},
                              'channel_id':channel['id'],'question':'核对路径两端符号和本页图例含义，判定是否保护业务；图例文字不清则说明缺失证据，不猜速率','status':'待复查'})
        for pair in g.get('parallel_expression_hypotheses',[]):
            tasks.append({'kind':'双线身份','page':page['page'],'hypothesis':pair,
                          'question':'两条图元是否为一个专用纤芯表达？结合图例、业务套数与编号核对；不可只凭平行距离合并','status':'待复查'})
        for edge in g.get('unbound_lines',[]):
            tasks.append({'kind':'未绑定连线','page':page['page'],'geometry':edge,'question':'核对线端、转折、交叉连接点和图例，保留多个可能路径','status':'待复查'})
    graph={'schema_version':1,'ontology':{'version':CATALOG['version'],'types':TYPES,'relations':RELATIONS,'legacy_projection':True,'typed_catalog':'config/engineering-ontology.json','legacy_type_mapping':CATALOG['legacy_types']},'rules':RULES,
           'objects':objects,'occurrences':occurrences,'claims':claims,'relations':relations,'identity_decisions':decisions,
           'limits':['实体保持独立候选，不自动跨页合并','未实现完整端口/纤芯实体抽取','复查任务尚未自动闭环确认']}
    for task in tasks:task['id']='Q-'+hashlib.sha256(json.dumps(task,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    save(folder/'engineering/graph.json',graph);save(folder/'engineering/review-tasks.json',tasks)
    return graph,tasks

if __name__=='__main__':
    import sys
    g,t=build_graph(sys.argv[1]);print(json.dumps({'objects':len(g['objects']),'identity_decisions':len(g['identity_decisions']),'review_tasks':len(t)}))
