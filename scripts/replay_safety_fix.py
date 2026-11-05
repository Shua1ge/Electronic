"""Repair three documented implementation defects after frozen validation.

Frozen outputs stay intact. Replay numeric checks; re-run notes only if the old
pipeline incorrectly split an unrecognized whole-page context into paragraphs.
"""
import json,shutil,subprocess,sys,time
from pathlib import Path
from full_pdf import save,sha256
from routing_rules import resolve_route

root=Path(__file__).resolve().parents[1]
source,dest=[Path(a).resolve() for a in sys.argv[1:3]]
state=json.loads((source/'validation.json').read_text())
changes={name:{'frozen':digest,'current':sha256(root/'scripts'/name)} for name,digest in state['code'].items() if sha256(root/'scripts'/name)!=digest}
assert set(changes)=={'channel_tasks.py','evidence_pdf.py','routing_rules.py'},changes
assert not dest.exists(),'Use a new safety-replay output directory'
dest.mkdir(parents=True)
summary={'source_validation':str(source),'independent_new_validation':False,
         'changes_explained':['Reject rates parsed inside ASCII OCR tokens; I0M must not produce 0M. Unknown rate stays blank.',
                             'Only subdivide verified optical sections; do not split whole-page fallback text into optical tasks.',
                             'Recognize verified title-block channel-allocation drawings as business diagrams.'],
         'code_changes':changes,'documents':[]}
for entry in state['documents']:
    name=entry['name'];old=source/name;new=dest/name
    affected=[];rerouted=[]
    for p in (old/'pages').glob('*.json'):
        page=json.loads(p.read_text())
        if page['route']['kind']=='设计说明' and any(t.get('region','').startswith('paragraph-') for t in page['tasks']) and not (old/'assets'/p.stem/'section-光路.json').exists():affected.append(page['page'])
        assets=json.loads((old/'assets'/p.stem/'ocr.json').read_text())
        if resolve_route(page['route'],assets['lines'])['kind']!=page['route']['kind']:rerouted.append(page['page'])
    if affected or rerouted:
        args=[sys.executable,str(root/'scripts/refine_routing.py'),str(old),'--output',str(new),'--diagram-regions','--channel-geometry']
        if affected:args+=['--refresh-kind','设计说明']
        subprocess.run(args,check=True,cwd=root)
        shutil.copytree(old/'audit',new/'audit')
    else:shutil.copytree(old,new)
    backup=new/'before-safety-fix';backup.mkdir()
    for filename in ['observations.json','structured.json','run.json']:shutil.copy2(old/filename,backup/filename)
    start=time.perf_counter()
    subprocess.run([sys.executable,str(root/'scripts/revalidate_evidence.py'),str(new)],check=True,cwd=root)
    # Keep coverage's correction list in sync with the final deterministic pass.
    for page_path in (new/'pages').glob('*.json'):
        page=json.loads(page_path.read_text());coverage=new/'assets'/page_path.stem/'channels/coverage.json'
        if not coverage.exists():continue
        data=json.loads(coverage.read_text())
        for c in data['channels']:
            rows=[r for r in page['extraction']['records'] if r.get('channel_id')==c['channel']]
            c['field_changes']=[f for r in rows for f in r.get('geometry_field_changes',[])]
        save(coverage,data)
    run=json.loads((new/'run.json').read_text())
    run.update(safety_replay={'source_run':str(old),'refreshed_note_pages':affected,'rerouted_pages':rerouted,'code_changes':changes,'replay_seconds':time.perf_counter()-start})
    save(new/'run.json',run)
    subprocess.run([sys.executable,str(root/'scripts/audit_corpus.py'),str(new)],check=True,cwd=root)
    for part,key in [('results.json','final_complete_matches'),('channel-results.json','strict_matches')]:
        before=json.loads((old/'audit'/part).read_text())[key];after=json.loads((new/'audit'/part).read_text())[key]
        if not affected and not rerouted:assert before==after,(name,part,before,after)
    # Raw model output bytes must be unchanged by this replay.
    for raw in (old/'channel-calls').rglob('*.json') if (old/'channel-calls').exists() else []:
        assert sha256(raw)==sha256(new/raw.relative_to(old))
    subprocess.run([sys.executable,str(root/'scripts/export_document.py'),str(new)],check=True,cwd=root)
    review=new/'review.html';text=review.read_text()
    banner=f"<p class='notice'>这是冻结评测后的修正版，不算新的独立泛化评测。修复I0M→0M误补值，真实带宽未知时留空；纠正章节分段与通道分配图分类，说明页 {affected} 和改分类页 {rerouted} 已重跑，其余模型回答复用。模型耗时为累计值。下方问题清单描述冻结版发现，修正前输出保存在before-safety-fix。</p>"
    text=text.replace('<h1>',banner+'<h1>',1);review.write_text(text)
    summary['documents'].append({'name':name,'output':str(new),'refreshed_note_pages':affected,'rerouted_pages':rerouted,'retained_channel_answers_unchanged':True,
                                 'facts_before':json.loads((old/'audit/results.json').read_text())['final_complete_matches'],
                                 'facts_after':json.loads((new/'audit/results.json').read_text())['final_complete_matches'],
                                 'channels_before':json.loads((old/'audit/channel-results.json').read_text())['strict_matches'],
                                 'channels_after':json.loads((new/'audit/channel-results.json').read_text())['strict_matches']})
save(dest/'replay.json',summary)
(dest/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>验证后修正版</title><h1>验证后修正版</h1><p>修复OCR数字截取、未识别章节仍拆光路段落及通道分配图分类的问题。原始回答和冻结评测保持不变；只重跑受影响的说明页和改分类页。这些PDF已参与修正，不能算新的独立泛化验证。</p><ul>'+''.join(f"<li><a href='{d['name']}/review.html'>{d['name']} 图文复核</a> · <a href='{d['name']}/structured.xlsx'>Excel</a> · 指定通道字段匹配：{d['channels_before']} → {d['channels_after']}</li>" for d in summary['documents'])+'</ul><a href="replay.json">修正记录</a>')
print(json.dumps(summary,ensure_ascii=False,indent=2))
