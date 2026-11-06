#!/usr/bin/env python3
"""Loopback-only drawing review server. Human edits persist in graph.json.

No external services, no spreadsheet import, no automatic engineering approval.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from drawing_graph import center, export_xlsx, norm_box, read_graph, write_json


class ConflictError(ValueError):pass


def apply_operations(original, revision, operations):
    if revision != original["revision"]:raise ConflictError("数据已被其他窗口修改，请刷新后重新编辑")
    if not isinstance(operations,list) or not 1<=len(operations)<=500:raise ValueError("修改数量无效")
    graph=copy.deepcopy(original)
    for op in operations:
        if not isinstance(op,dict):raise ValueError("修改内容必须为对象")
        page=next((p for p in graph["pages"] if p["id"]==op.get("page")),None)
        if page is None:raise ValueError("页码不存在")
        objects={o["id"]:o for o in page["objects"]}
        connections={c["id"]:c for c in page["connections"]}
        action=op.get("action")
        if action in {"object_update","object_add"}:
            if action=="object_add":
                point=op.get("point")
                if not isinstance(point,list) or len(point)!=2 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0<=v<=1 for v in point):raise ValueError("位置无效")
                oid=f"p{page['id']:03d}-M{len(page['objects'])+1:04d}"
                obj={"id":oid,"page":page["id"],"kind":"external","bbox":norm_box([point[0]-.002,point[1]-.003,point[0]+.002,point[1]+.003]),
                     "center":point,"parent_id":None,"name":None,"name_options":[],"name_state":"human_corrected","source":"human"}
                page["objects"].append(obj);objects[oid]=obj
            else:
                obj=objects.get(op.get("id"))
                if obj is None:raise ValueError("对象不存在")
            changes=op.get("changes",{})
            if not isinstance(changes,dict) or set(changes)-{"name","parent_id","kind"}:raise ValueError("对象修改字段无效")
            if "name" in changes:
                name=changes["name"]
                if name is not None and (not isinstance(name,str) or len(name)>120):raise ValueError("名称过长或类型错误")
                obj["name"]=name.strip() if name else None;obj["name_state"]="human_corrected"
            if "kind" in changes:
                if changes["kind"] not in {"station","station_box","equipment","cabinet","unit","port","external"}:raise ValueError("对象类型无效")
                obj["kind"]=changes["kind"]
            if "parent_id" in changes:
                parent=changes["parent_id"] or None
                if parent is not None and parent not in objects:raise ValueError("父对象不存在")
                seen={obj["id"]};cur=parent
                while cur:
                    if cur in seen:raise ValueError("父子关系存在循环")
                    seen.add(cur);cur=objects[cur].get("parent_id")
                obj["parent_id"]=parent
            # Previously approved downstream records need another explicit confirmation.
            descendants={obj["id"]}
            for _ in range(len(objects)):
                before=len(descendants);descendants|={o["id"] for o in objects.values() if o.get("parent_id") in descendants}
                if len(descendants)==before:break
            for c in page["connections"]:
                if c["a"] in descendants or c["z"] in descendants:
                    if c["status"]=="approved":c["status"]="corrected"
        elif action in {"connection_update","connection_add"}:
            changes=op.get("changes",{})
            if not isinstance(changes,dict) or set(changes)-{"a","z","path_id","relation","attributes","notes"}:raise ValueError("连接修改字段无效")
            if action=="connection_add":
                cid=f"p{page['id']:03d}-H{len(page['connections'])+1:04d}"
                conn={"id":cid,"page":page["id"],"a":None,"z":None,"path_id":None,"relation":"wiring",
                      "attributes":{},"attribute_evidence":[],"attribute_state":"human_entered","status":"corrected","notes":[],"source":"human"}
                page["connections"].append(conn)
            else:
                conn=connections.get(op.get("id"))
                if conn is None:raise ValueError("连接不存在")
            old=(conn["a"],conn["z"])
            for field in ("a","z"):
                if field in changes:
                    if changes[field] not in objects:raise ValueError("连接端点不存在")
                    conn[field]=changes[field]
            if not conn["a"] or not conn["z"] or conn["a"]==conn["z"]:raise ValueError("需要两个不同端点")
            if old!=(conn["a"],conn["z"]) and action=="connection_update":
                conn["path_id"]=None;conn["attribute_state"]="needs_recheck"
                conn["notes"]=list(dict.fromkeys(conn["notes"]+["端点已修改，请检查路径及参数归属"]))
            if "path_id" in changes:
                path=changes["path_id"] or None
                if path is not None and not any(p["id"]==path for p in page["paths"]):raise ValueError("路径不存在")
                conn["path_id"]=path
            if "attributes" in changes:
                attrs=changes["attributes"]
                if not isinstance(attrs,dict) or len(attrs)>30:raise ValueError("参数格式错误")
                for k,v in attrs.items():
                    if not isinstance(k,str) or len(k)>80 or isinstance(v,(list,dict,bool)) or (isinstance(v,str) and len(v)>500) or (isinstance(v,(int,float)) and not math.isfinite(v)):raise ValueError("参数值无效")
                    if k in {"cores","length_km"} and v is not None and (not isinstance(v,(int,float)) or v<=0 or (k=="cores" and int(v)!=v)):raise ValueError("芯数和长度需要正数")
                conn["attributes"]=attrs;conn["attribute_state"]="human_corrected"
            if "relation" in changes:
                if changes["relation"] not in {"cable","wiring","logical","unknown"}:raise ValueError("连接类别无效")
                conn["relation"]=changes["relation"]
            if "notes" in changes:
                if not isinstance(changes["notes"],list) or not all(isinstance(s,str) and len(s)<=500 for s in changes["notes"]):raise ValueError("备注格式错误")
                conn["notes"]=changes["notes"]
            conn["status"]="corrected"
        elif action=="status":
            ids=op.get("ids",[]);status=op.get("status")
            if status not in {"candidate","approved","rejected"}:raise ValueError("审核状态无效")
            if not isinstance(ids,list) or not ids or any(i not in connections for i in ids):raise ValueError("候选ID无效")
            for cid in ids:connections[cid]["status"]=status
        else:raise ValueError("未知修改操作")
    graph["revision"]+=1
    graph.setdefault("events",[]).append({"revision":graph["revision"],"time":datetime.now(timezone.utc).isoformat(),
        "actor":"local-human-review","operations":copy.deepcopy(operations)})
    return graph


class ReviewStore:
    def __init__(self,root):
        self.root=Path(root).resolve();self.lock=threading.Lock()
        if (self.root/"batch.json").is_file():self.projects=read_graph(self.root/"batch.json")["projects"]
        elif (self.root/"graph.json").is_file():self.projects=[{"id":"drawing","name":read_graph(self.root/"graph.json")["input"]["name"]}]
        else:raise ValueError("目录中没有batch.json或graph.json")
    def folder(self,project):
        if project not in {p["id"] for p in self.projects}:raise ValueError("图纸不存在")
        dest=self.root if project=="drawing" and (self.root/"graph.json").exists() else (self.root/project).resolve()
        if not dest.is_relative_to(self.root):raise ValueError("路径无效")
        return dest
    def save(self,project,revision,operations):
        with self.lock:
            folder=self.folder(project)
            graph=apply_operations(read_graph(folder/"graph.json"),revision,operations)
            write_json(folder/"graph.json",graph)
            return graph
    def export(self,project):
        with self.lock:
            folder=self.folder(project);graph=read_graph(folder/"graph.json")
            temp=folder/"structured.tmp.xlsx";export_xlsx(graph,temp);temp.replace(folder/"structured.xlsx")
            return (folder/"structured.xlsx").read_bytes()


def make_handler(store,token):
    web=Path(__file__).resolve().parents[1]/"web"/"drawing-review"
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,body,kind="application/json; charset=utf-8",headers=None):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status);self.send_header("Content-Type",kind);self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff")
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)
        def do_GET(self):
            try:
                url=urlparse(self.path);query=parse_qs(url.query);project=query.get("project",[store.projects[0]["id"]])[0]
                if url.path=="/api/session":return self.send(200,{"token":token,"projects":store.projects})
                if url.path=="/api/graph":return self.send(200,read_graph(store.folder(project)/"graph.json"))
                if url.path=="/api/export":return self.send(200,store.export(project),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",{"Content-Disposition":"attachment; filename=structured.xlsx"})
                if url.path.startswith("/files/"):
                    parts=unquote(url.path).split("/",3)
                    if len(parts)!=4:raise ValueError("文件路径无效")
                    root=store.folder(parts[2]);file=(root/parts[3]).resolve()
                    if not file.is_relative_to(root):raise ValueError("文件路径无效")
                else:
                    name="index.html" if url.path=="/" else unquote(url.path).lstrip("/")
                    file=(web/name).resolve()
                    if not file.is_relative_to(web):raise ValueError("路径无效")
                if not file.is_file():return self.send(404,{"error":"文件不存在"})
                return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            except (ValueError,OSError) as e:self.send(400,{"error":str(e)})
        def do_POST(self):
            if urlparse(self.path).path!="/api/save":return self.send(404,{"error":"接口不存在"})
            if self.headers.get("X-Review-Token")!=token:return self.send(403,{"error":"会话无效，请刷新页面"})
            origin=self.headers.get("Origin")
            if origin and origin!=f"http://{self.headers.get('Host')}":return self.send(403,{"error":"来源无效"})
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<2_000_000:raise ValueError("请求大小无效")
                data=json.loads(self.rfile.read(length));project=parse_qs(urlparse(self.path).query).get("project",[store.projects[0]["id"]])[0]
                self.send(200,store.save(project,data.get("revision"),data.get("operations")))
            except ConflictError as e:self.send(409,{"error":str(e)})
            except (ValueError,KeyError,TypeError,OSError) as e:self.send(400,{"error":str(e)})
    return Handler


def serve(root,port=8765):
    store=ReviewStore(root);server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(store,secrets.token_urlsafe(24)))
    print(f"图表联动复核：http://127.0.0.1:{server.server_port} （Ctrl+C停止）",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("output",type=Path);p.add_argument("--port",type=int,default=8765)
    args=p.parse_args();serve(args.output,args.port)
