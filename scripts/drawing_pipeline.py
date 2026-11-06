#!/usr/bin/env python3
"""Local Qwen3.5-2B object/port/path extraction with one reviewable graph.

Example: python scripts/drawing_pipeline.py a.pdf b.pdf --output new-run --serve
The macOS Audit and Fast v0.3 commands remain available.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import time
from pathlib import Path

from drawing_graph import (VERSION, build_page, center, distance, export_xlsx, norm_box,
                           point_segment, read_graph, write_json)

NAME_PROMPT = """这是工程连接图中一个目标对象的局部。目标类型为{kind}，红框/红点表示目标位置。
只逐字读取目标的名称或编号。设备名称通常位于设备框上方；端口只读紧邻红点的端口/端子编号，不读整柜名称。站点只读紧邻符号的站名。
不要根据常识改写生僻字，不补全不可见编号，不把范围展开成不存在的端口。没有可读名称就返回null。
只返回JSON：{{"name":null,"alternatives":[],"unclear":false}}。多种读法保留在alternatives。"""

LABEL_PROMPT = """这是工程连接图的一条候选线路局部，红线标示本次目标路径。
只抄录紧邻目标路径、沿着目标路径的线路文字标注。不要读相邻其他线路的标注；无法判断归属的文字也可保留，但uncertain填true。
不要推测连接两端，不根据常识填长度、类型或芯数。端口编号不要当长度。
每次最多抄录3条标注。只返回JSON对象：{"labels":[{"text":"可见标注原文","bbox":[0,0,1000,1000],"uncertain":false}]}。
bbox是本张图内文字框坐标，左上为0,0，右下为1000,1000。没有可读标注返回{"labels":[]}。"""


def json_reply(raw, allow_label_list=False):
    text=raw.strip()
    if text.startswith("```"):
        text=re.sub(r"^```(?:json)?\s*","",text,flags=re.I)
        if text.endswith("```"):text=text[:-3].strip()
    obj=json.loads(text)
    if allow_label_list and isinstance(obj,list):obj={"labels":obj}
    if not isinstance(obj,dict):raise ValueError("模型回答必须为JSON对象")
    return obj


def render_task(page, box, path, *, marker=None, points=None):
    """Render directly from PDF; don't enlarge an already downsampled overview."""
    import pymupdf
    from PIL import Image, ImageDraw
    w,h=page.rect.width,page.rect.height
    box=norm_box(box);clip=pymupdf.Rect(box[0]*w,box[1]*h,box[2]*w,box[3]*h)&page.rect
    scale=min(8,1800/max(clip.width,clip.height))
    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=clip,alpha=False)
    original=Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    marked=original.copy();draw=ImageDraw.Draw(marked)
    def xy(p):return ((p[0]*w-clip.x0)*scale,(p[1]*h-clip.y0)*scale)
    if marker:
        if len(marker)==4:
            a,b=xy(marker[:2]),xy(marker[2:]);draw.rectangle((*a,*b),outline="#e33434",width=3)
        else:
            x,y=xy(marker);draw.ellipse((x-7,y-7,x+7,y+7),outline="#e33434",width=3)
    if points:draw.line([xy(p) for p in points],fill="#ed3030",width=3)
    pair=Image.new("RGB",(original.width,original.height*2+22),"white")
    pair.paste(original,(0,0));pair.paste(marked,(0,original.height+22))
    pair.save(path)
    # The model sees one marked crop, avoiding duplicate transcription of a pair.
    # Both the original and paired evidence remain available to the reviewer.
    original.save(path.with_name(path.stem+"-raw.png"))
    marked.save(path.with_name(path.stem+"-model.png"))
    return box


def object_task_box(obj):
    b=obj["bbox"];x,y=obj["center"]
    if obj["kind"]=="port":
        return [x-.025,y-.022,x+.025,y+.022]
    if obj["kind"] in {"station","station_box"}:
        return [x-.12,y-.065,x+.12,y+.065]
    # Read the header and top of a cabinet/unit at high resolution.
    return [b[0]-.025,b[1]-.045,b[2]+.025,min(b[3],b[1]+.09)]


def apply_name(obj, parsed, call_id):
    name=parsed.get("name")
    name=name.strip()[:120] if isinstance(name,str) and name.strip() else None
    alts=parsed.get("alternatives",[])
    if not isinstance(alts,list):alts=[]
    alts=list(dict.fromkeys(s.strip()[:120] for s in alts if isinstance(s,str) and s.strip() and s.strip()!=name))[:5]
    obj.update(name=name,name_options=alts,
               name_state="uncertain" if parsed.get("unclear") or alts else ("qwen_candidate" if name else "unread"),
               name_call_id=call_id)


def parse_attributes(text):
    attrs={}
    typ=re.search(r"(?<![A-Za-z])(OPGW|ADSS|OPPC|GYTA(?:\d+[A-Za-z0-9-]*)?)(?![A-Za-z])",text,re.I)
    cores=re.search(r"(?<!\d)(\d{1,4})\s*芯",text)
    km=re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:km|千米)(?![A-Za-z])",text,re.I)
    if typ:attrs["type"]=typ[1].upper()
    if cores:attrs["cores"]=int(cores[1])
    if km:attrs["length_km"]=float(km[1])
    return attrs


def recorded_source(root, pdf, digest):
    """Select a batch replay by input hash, never by a guessed station/file name."""
    root=Path(root).resolve()
    if (root/"batch.json").is_file():
        for project in read_graph(root/"batch.json")["projects"]:
            folder=(root/project["id"]).resolve()
            if not folder.is_relative_to(root):raise ValueError("回放目录无效")
            saved=read_graph(folder/"graph.json")
            if saved["input"]["sha256"]==digest:return folder/"calls",saved
        raise ValueError("回放批次中没有与输入PDF哈希一致的结果")
    folder=root/"calls" if (root/"graph.json").is_file() else root
    metadata=folder.parent/"graph.json"
    if metadata.is_file():
        saved=read_graph(metadata)
        if saved["input"]["sha256"]!=digest:raise ValueError("回放回答不属于当前PDF")
        return folder,saved
    return (root/pdf.stem if (root/pdf.stem).is_dir() else root),None


def apply_labels(page, connection, parsed, crop_box, call_id):
    paths={p["id"]:p for p in page["paths"]};target=paths[connection["path_id"]]
    labels=parsed.get("labels",[])
    if not isinstance(labels,list):raise ValueError("labels必须为数组")
    # Accept equivalent *valid* JSON envelopes from the small model. This does
    # not repair truncated JSON or invent missing text/boxes/parameters.
    pending=list(labels);labels=[]
    for _ in range(100):
        if not pending:break
        item=pending.pop(0)
        if isinstance(item,dict) and isinstance(item.get("text"),str):labels.append(item)
        elif isinstance(item,dict) and isinstance(item.get("labels"),list):pending.extend(item["labels"])
        elif isinstance(item,list):pending.extend(item)
    evidence=[];proposals={};conflicts=[]
    for item in labels[:20]:
        if not isinstance(item,dict) or not isinstance(item.get("text"),str):continue
        text=item["text"][:300];bbox=item.get("bbox")
        valid=isinstance(bbox,list) and len(bbox)==4 and all(isinstance(v,(int,float)) and not isinstance(v,bool) and 0<=v<=1000 for v in bbox)
        valid=valid and bbox[0]<bbox[2] and bbox[1]<bbox[3]
        box=None;associated=False
        if valid:
            x0,y0,x1,y1=crop_box
            box=[x0+bbox[0]/1000*(x1-x0),y0+bbox[1]/1000*(y1-y0),
                 x0+bbox[2]/1000*(x1-x0),y0+bbox[3]/1000*(y1-y0)]
            c=center(box)
            def pd(path):return min(point_segment(c,a,b)[0] for a,b in zip(path["points"],path["points"][1:]))
            d=pd(target)
            rivals=[pd(p) for p in page["paths"] if p["id"]!=target["id"] and p.get("state")=="bound"]
            associated=d<.025 and (not rivals or d+.002<min(rivals)) and not item.get("uncertain")
        attrs=parse_attributes(text)
        evidence.append({"text":text,"bbox":box,"bbox_source":"qwen_proposal","call_id":call_id,
                         "association":"spatial_candidate" if associated else "ambiguous", "values":attrs})
        # Values remain useful prefilled candidates; ambiguity is shown separately.
        for key,value in attrs.items():
            if key in proposals and proposals[key]!=value:conflicts.append(key)
            else:proposals[key]=value
    if page["kind"]=="cabinet" and evidence:
        # Wire numbers and fiber ranges are useful without cable-specific fields.
        # Keep literal ranges; do not expand them into invented individual wires.
        proposals["label"]="；".join(dict.fromkeys(e["text"] for e in evidence))[:450]
    connection["attributes"]=proposals
    connection["attribute_evidence"]=evidence
    connection["attribute_state"]=("conflict" if conflicts else "spatial_candidate" if evidence and all(e["association"]=="spatial_candidate" for e in evidence) else "ambiguous" if evidence else "unread")
    if conflicts:connection["notes"].append("标注存在不同参数："+",".join(sorted(set(conflicts))))


def extract(pdf, out, args, backend=None):
    import pymupdf
    started=time.monotonic();pdf=pdf.expanduser().resolve()
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    replies,saved_run=recorded_source(args.responses_dir,pdf,digest) if args.mode=="replay" else (None,None)
    if saved_run and saved_run.get("code_sha256",{}).get("drawing_graph.py") not in (None,hashlib.sha256(Path(__file__).with_name("drawing_graph.py").read_bytes()).hexdigest()):
        raise ValueError("几何算法已变化，不能按旧对象ID回放；请重新抽取")
    out.mkdir(parents=True)
    (out/"pages").mkdir();(out/"tasks").mkdir();(out/"calls").mkdir()
    graph={"version":VERSION,"revision":0,"input":{"name":pdf.name,"sha256":digest},
           "model":{"id":args.model_id,"mode":args.mode,"calls":0},"pages":[],"events":[],"issues":[]}
    if saved_run:
        graph["replay_origin"]={"input":saved_run["input"],"model":saved_run["model"],"timing":saved_run.get("timing")}
    with pymupdf.open(pdf) as doc:
        for i,page in enumerate(doc,1):
            print(f"[{pdf.name}] p{i} geometry",flush=True)
            pg=build_page(page,i,args.kind)
            pg["image"]=f"pages/page{i:03d}.png"
            pix=page.get_pixmap(matrix=pymupdf.Matrix(3000/max(page.rect.width,page.rect.height),3000/max(page.rect.width,page.rect.height)),alpha=False)
            pix.save(out/pg["image"]);graph["pages"].append(pg)
            print(f"  {pg['kind']}: {len(pg['objects'])} objects, {len(pg['connections'])} connections, {len(pg['paths'])} paths",flush=True)
            if args.mode=="geometry":continue
            tasks=[]
            for obj in pg["objects"]:
                tasks.append(("name",obj,object_task_box(obj)))
            for c in pg["connections"]:
                points=next(p["points"] for p in pg["paths"] if p["id"]==c["path_id"])
                # One segment crop keeps long bent routes from including half the page.
                a,b=max(zip(points,points[1:]),key=lambda ab:distance(*ab))
                points=[a,b]
                xs,ys=zip(*points)
                tasks.append(("label",c,[min(xs)-.025,min(ys)-.025,max(xs)+.025,max(ys)+.025]))
            for task_kind,item,box in tasks:
                call_id=item["id"]+"-"+task_kind
                if args.max_calls and graph["model"]["calls"]>=args.max_calls:
                    graph["issues"].append({"task":call_id,"reason":"call_budget","page":i});continue
                image=out/"tasks"/(call_id+".png")
                if task_kind=="name":
                    marker=item["center"] if item["kind"]=="port" else item["bbox"]
                    box=render_task(page,box,image,marker=marker)
                    prompt=NAME_PROMPT.format(kind=item["kind"])
                else:
                    points=next(p["points"] for p in pg["paths"] if p["id"]==item["path_id"])
                    box=render_task(page,box,image,points=points);prompt=LABEL_PROMPT
                item["evidence_image"]="tasks/"+image.name
                print(f"[{pdf.name}] {call_id} {graph['model']['calls']+1}/{len(tasks)}",flush=True)
                model_image=image.with_name(image.stem+"-model.png")
                result={"call_id":call_id,"prompt":prompt,"image":"tasks/"+image.name,"model_image":"tasks/"+model_image.name,"crop_bbox":box}
                try:
                    if args.mode=="replay":
                        source=replies/(call_id+".json")
                        saved=read_graph(source)
                        if saved.get("prompt") and saved["prompt"]!=prompt:raise ValueError("任务提示词与原调用不同，不能直接回放")
                        if saved.get("model_image"):
                            old_image=source.parent.parent/saved["model_image"]
                            if not old_image.is_file() or hashlib.sha256(old_image.read_bytes()).digest()!=hashlib.sha256(model_image.read_bytes()).digest():raise ValueError("回放图像与原调用不同")
                        reply={k:saved[k] for k in ("raw","seconds","generated_tokens","max_tokens_reached","peak_reserved_gib") if k in saved} if "raw" in saved else {"raw":json.dumps(saved,ensure_ascii=False)}
                        result["source_call"]=str(source)
                    else:
                        backend.max_new_tokens=min(args.max_new_tokens,112) if task_kind=="name" else args.max_new_tokens
                        reply=backend.infer(model_image,graph["model"]["calls"],prompt=prompt)
                    result.update(reply);parsed=json_reply(reply["raw"],allow_label_list=task_kind=="label")
                    if task_kind=="name":apply_name(item,parsed,call_id)
                    else:apply_labels(pg,item,parsed,box,call_id)
                    if reply.get("max_tokens_reached"):graph["issues"].append({"task":call_id,"reason":"token_limit","page":i})
                except Exception as ex:
                    result["error"]=f"{type(ex).__name__}: {ex}"
                    item["model_error"]=result["error"]
                    graph["issues"].append({"task":call_id,"reason":result["error"],"page":i})
                    if "out of memory" in str(ex).lower() and backend is not None:backend.torch.cuda.empty_cache()
                graph["model"]["calls"]+=1
                write_json(out/"calls"/(call_id+".json"),result)
                write_json(out/"graph.json",graph)
    graph["timing"]={"total_seconds":round(time.monotonic()-started,3)}
    graph["model"]["revision"]=getattr(getattr(getattr(backend,"model",None),"config",None),"_commit_hash",None)
    graph["code_sha256"]={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                          for name in ("drawing_pipeline.py","drawing_graph.py","fast_cable_route.py")}
    graph["statistics"]={"objects":sum(len(p["objects"]) for p in graph["pages"]),
        "connections":sum(len(p["connections"]) for p in graph["pages"]),
        "unbound_paths":sum(p.get("state")!="bound" for pg in graph["pages"] for p in pg["paths"]),
        "named_objects":sum(bool(o["name"]) for p in graph["pages"] for o in p["objects"])}
    write_json(out/"graph.json",graph);write_json(out/"initial_graph.json",graph)
    export_xlsx(graph,out/"structured.xlsx")
    return graph


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf",nargs="+",type=Path)
    p.add_argument("--output",required=True,type=Path,help="全新输出目录；支持一次处理多份PDF")
    p.add_argument("--mode",choices=["qwen","geometry","replay"],default="qwen")
    p.add_argument("--kind",choices=["auto","route","cabinet"],default="auto")
    p.add_argument("--model-id",default="Qwen/Qwen3.5-2B")
    p.add_argument("--responses-dir",type=Path)
    p.add_argument("--max-calls",type=int,default=0,help="每份PDF最多调用次数；0为全部，未处理任务会明确记录")
    p.add_argument("--max-new-tokens",type=int,default=256)
    p.add_argument("--online",action="store_true")
    p.add_argument("--serve",action="store_true")
    p.add_argument("--port",type=int,default=8765)
    return p


def main():
    args=parser().parse_args()
    if args.output.exists():raise SystemExit("输出目录已存在，请更换目录，避免覆盖审核结果")
    if args.max_calls<0 or not 64<=args.max_new_tokens<=2048:raise SystemExit("调用预算或token预算无效")
    if args.mode=="replay" and (not args.responses_dir or not args.responses_dir.is_dir()):raise SystemExit("replay模式需要--responses-dir")
    for pdf in args.pdf:
        if not pdf.expanduser().is_file() or pdf.suffix.lower()!=".pdf":raise SystemExit(f"PDF不存在：{pdf}")
    backend=None
    if args.mode=="qwen":
        from fast_cable_route import CudaQwen
        print("加载 Qwen3.5 2B NF4；批次内共用模型",flush=True)
        backend=CudaQwen(args.model_id,args.online,args.max_new_tokens,42)
    args.output.mkdir(parents=True)
    projects=[]
    for index,pdf in enumerate(args.pdf,1):
        slug=f"{index:02d}-"+re.sub(r"[^\w-]","_",pdf.stem)
        graph=extract(pdf,args.output/slug,args,backend)
        projects.append({"id":slug,"name":pdf.name,"statistics":graph["statistics"]})
        write_json(args.output/"batch.json",{"projects":projects})
    print(json.dumps(projects,ensure_ascii=False,indent=2))
    print(f"复核命令：{sys.executable} {Path(__file__).with_name('review_server.py')} {args.output.resolve()}")
    if args.serve:
        from review_server import serve
        serve(args.output,args.port)


if __name__=="__main__":main()
