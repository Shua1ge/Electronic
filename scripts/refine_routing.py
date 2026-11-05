"""Automatically re-route a completed evidence run using verified title blocks.

No audit data is read. Unaffected outputs are reused, not billed as new inference.
The source run stays intact; every changed task has fresh local model responses.
"""
import argparse
import hashlib
import json
import shutil
import time
from collections import Counter
from pathlib import Path

from document_core import ROOT
from evidence_core import decode, safe_aggregate, validate_records
from evidence_pdf import ROUTES, extraction_prompt, output_schema, select_context,select_units,task_prompt
from full_pdf import save, sha256
from routing_rules import resolve_route
from page_orientation import normalize_assets
from graphic_regions import detect_regions
from channel_tasks import extract_geometry
from project_context import build_context


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path);parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--diagram-regions",action="store_true")
    parser.add_argument("--channel-geometry",action="store_true")
    parser.add_argument("--refresh-kind",choices=list(ROUTES),action="append",default=[],help="Re-run selected page kinds for development; no audit labels are read")
    args=parser.parse_args();src=args.source.resolve();out=args.output.resolve()
    original=json.loads((src/"run.json").read_text())
    if original["status"] not in {"complete","partial"}:raise ValueError("source run must be finished")
    if src==out:raise ValueError("keep the original run intact")
    if sha256(Path(original["input"]))!=original["signature"]["pdf_sha256"]:raise ValueError("source PDF changed")
    version=hashlib.sha256(b''.join((ROOT/'scripts'/name).read_bytes() for name in
                          ['refine_routing.py','evidence_pdf.py','evidence_core.py','routing_rules.py','page_orientation.py','graphic_regions.py','channel_geometry.py','channel_tasks.py','drawing_topology.py','project_context.py','engineering_ontology.py','ontology_extraction.py'])+(ROOT/'config/engineering-ontology.json').read_bytes()).hexdigest()
    if (out/'run.json').exists() and json.loads((out/'run.json').read_text()).get('refinement_code_sha256')!=version:
        raise ValueError('Refinement code changed; use a new output directory')
    if not out.exists():
        # Do not carry prior audit assertions into a new result with different IDs.
        shutil.copytree(src,out,ignore=shutil.ignore_patterns("audit","structured*","review.html","run.json","process.log"))
        for folder in ["pages","assets","calls"]:
            for p in (out/folder).rglob("*.json"):
                p.write_text(p.read_text().replace(str(src),str(out)))
    save(out/"source-run.json",original)
    manifest={**original,"status":"running","source_run":str(src),"routing_refinement":True,
              "refinement_code_sha256":version,
              "diagram_region_refinement":args.diagram_regions,
              "channel_geometry_refinement":args.channel_geometry,
              "routing_rules_sha256":sha256(ROOT/"scripts/routing_rules.py")}
    save(out/"run.json",manifest)
    changes=[];tasks=[];pages=[]
    for p in sorted((out/"pages").glob("*.json")):
        page=json.loads(p.read_text());n=page["page"]
        assets=json.loads((out/"assets"/f"{n:03d}"/"ocr.json").read_text())
        assets,rotated=normalize_assets(assets,out,n)
        page['rotation_ccw']=assets.get('rotation_ccw',0)
        if rotated:
            by_id={r['id']:r for r in assets['lines']}
            for r in (page['extraction'] or {}).get('records',[]):
                for check in r.get('field_evidence',{}).values():
                    for source in check['sources']:
                        source['original_bbox']=source['bbox'];source['bbox']=by_id[source['id']]['bbox']
        updated=resolve_route(page["route"],assets["lines"])
        # A resumed refinement may have committed this page already.
        if updated and updated.get("routing_override"):
            changes.append({"page":n,"from":updated["model_kind"],"to":updated["kind"],"title":updated["title"],"evidence":updated["routing_override"]})
        if updated and (updated['kind'] in args.refresh_kind or updated["kind"]!=page["route"]["kind"] or rotated or (args.channel_geometry and updated['kind']=='业务组织' and not page.get('channel_geometry_completed')) or (args.diagram_regions and updated['kind']=='业务组织' and not page.get('diagram_regions_completed'))):
            tasks.append((page,updated,assets))
        pages.append(page)
    save(out/"routing-changes.json",changes)
    print(f"Checked ALL {len(pages)} page titles; {len(tasks)} pages need different extraction tasks",flush=True)
    if tasks:
        import mlx.core as mx
        from mlx_vlm import load,generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
        from mlx_vlm.structured import build_json_schema_logits_processor
        cfg=json.loads((ROOT/"config/model.json").read_text());model_path=ROOT/cfg["local_directory"]
        if cfg["revision"]!=original["signature"]["model_revision"]:raise ValueError("model changed")
        model,processor=load(str(model_path));model_cfg=load_config(str(model_path))
        sampling=original["signature"]["sampling"]
    build_context(out)
    tasks.sort(key=lambda task:(task[1]['kind']!='设计说明',task[0]['page']))
    for page,route,assets in tasks:
        n=page["page"];print(f"Page {n}: {page['route']['kind']} -> {route['kind']}",flush=True)
        # Save previous page observations and decisions for review, not as new answers.
        save(out/"before-routing-refinement"/f"{n:03d}.json",page)
        records=[r for r in (page["extraction"] or {}).get("records",[]) if r["category"]=="项目"]
        statuses=[];rejected=[];unresolved=[]
        geometry=extract_geometry(page,assets,out,model,processor,model_cfg,sampling) if args.channel_geometry and route['kind']=='业务组织' else None
        if geometry is not None:
            records.extend(geometry['records']);statuses=geometry['tasks'];rejected=geometry['rejected_records'];unresolved=geometry['unresolved']
            page.update(route=route,tasks=statuses,rejected_records=rejected,page_complete=all(t['success'] for t in statuses),
                        extraction={'page_kind':route['kind'],'title':route['title'],'records':records,'unresolved':unresolved},
                        geometry_coverage=geometry['coverage'],channel_geometry_completed=True,diagram_regions_completed=True)
            save(out/'pages'/f'{n:03d}.json',page)
            print(f"Page {n}: geometry coverage {geometry['coverage']}",flush=True)
            continue
        regions=detect_regions(assets,out/'assets'/f'{n:03d}'/'groups') if args.diagram_regions and route['kind']=='业务组织' else []
        page['regions']=[{'id':r['id'],'bbox':r['bbox'],'image':r['image'],'source_ids':[l['id'] for l in r['lines']]} for r in regions]
        units=[]
        for category in ROUTES[route["kind"]]:
            if len(regions)>1:
                units.extend((category,f"region-{r['id']}",r['lines'],[assets['overview'],r['image']]) for r in regions)
            else:
                for unit,selected,images in select_units(route['kind'],category,assets,out,n):
                    units.append((category,'refresh-whole' if unit=='whole' and route['kind'] in args.refresh_kind else unit,selected,images))
        for category,unit,selected,images in units:
            print(f"Page {n}: {category} {unit}",flush=True)
            prompt=task_prompt(category,selected,unit,out,n);parsed=None
            if unit.startswith('region-'):prompt+='\n本次只提取第二张放大图中的对象。第一张整页仅用于空间参考，不提取其他区域。一个图形组内有多少条符合类别的通道就输出多少，不把别类通道改写成本类。'
            for attempt in range(2):
                p=out/"routing-calls"/f"{n:03d}"/unit/f"{category}-{attempt}.json";p.parent.mkdir(parents=True,exist_ok=True)
                if p.exists() and p.with_suffix('.prompt.txt').exists() and p.with_suffix('.prompt.txt').read_text()==prompt:response=json.loads(p.read_text())
                else:
                    if p.exists():save(out/'before-task-refresh'/f'{n:03d}'/unit/p.name,json.loads(p.read_text()))
                    actual=prompt+("\n上次输出不完整；只输出完整JSON。" if attempt else "")
                    p.with_suffix('.prompt.txt').write_text(actual)
                    formatted=apply_chat_template(processor,model_cfg,actual,num_images=len(images),enable_thinking=False)
                    constrained=build_json_schema_logits_processor(processor.tokenizer,output_schema(category,selected))
                    mx.random.seed(0);started=time.perf_counter();limit=original["signature"]["max_tokens"]*(attempt+1)
                    answer=generate(model,processor,formatted,images,max_tokens=limit,verbose=False,logits_processors=[constrained],**sampling)
                    response={"raw":answer.text,"seconds":time.perf_counter()-started,"generation_tokens":answer.generation_tokens,
                              "prompt_tokens":answer.prompt_tokens,"token_limit":limit,"hit_token_limit":answer.generation_tokens>=limit,
                              "mlx_run_peak_gb":mx.get_peak_memory()/1e9,"images":images,"task":category,"attempt_index":attempt}
                    mx.clear_cache()
                try:
                    parsed=validate_records(response["raw"],category,selected)
                    if response["hit_token_limit"]:raise ValueError("generation reached token budget")
                    response["validation_error"]=None
                except (ValueError,TypeError) as error:
                    response["validation_error"]=str(error);parsed=None
                save(p,response);page["attempts"].append(response)
                if parsed is not None:break
            statuses.append({"category":category,"region":unit,"source_ids":[r['id'] for r in selected],"success":parsed is not None})
            if parsed:
                for row in parsed['records']:row['region']=unit
                records.extend(parsed["records"]);rejected.extend(parsed["rejected_records"]);unresolved.extend(parsed["unresolved"])
            else:unresolved.append(f"{category}提取失败")
        page.update(route=route,tasks=statuses,rejected_records=rejected,page_complete=all(t["success"] for t in statuses),
                    extraction={"page_kind":route["kind"],"title":route["title"],"records":records,"unresolved":unresolved})
        page['diagram_regions_completed']=bool(args.diagram_regions and route['kind']=='业务组织' and page['page_complete'])
        save(out/"pages"/f"{n:03d}.json",page)
        if route["kind"]=="设计说明":build_context(out)
        print(f"Page {n}: {len(records)} revised candidates",flush=True)
    observations=[{"id":f"P{p['page']:03d}R{i:03d}","page":p["page"],**r}
                  for p in pages for i,r in enumerate((p["extraction"] or {}).get("records",[]),1)]
    combined=safe_aggregate(observations);save(out/"observations.json",observations);save(out/"structured.json",combined)
    manifest.update(status="complete" if all(p["page_complete"] for p in pages) else "partial",
                    successful_pages=sum(p["page_complete"] for p in pages),observations=len(observations),output_records=len(combined["records"]),
                    category_counts=dict(Counter(r["category"] for r in combined["records"])),
                    conflicted_records=sum(bool(r["conflicts"]) for r in combined["records"]),candidate_links=0,
                    withheld_fields=sum(len(r["withheld_fields"]) for r in observations),
                    inference_seconds=sum(a["seconds"] for p in pages for a in p["attempts"]),
                    refinement_inference_seconds=sum(a["seconds"] for p in pages for a in p["attempts"])-original["inference_seconds"],
                    mlx_peak_gb=max(a["mlx_run_peak_gb"] for p in pages for a in p["attempts"]))
    save(out/"run.json",manifest)
    from export_document import export_run
    export_run(out);print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)


if __name__=="__main__":run()
