"""Shared spatial graph for route maps and equipment wiring; no sample answers.

Coordinates are normalized to the original PDF page. Names never identify objects.
Only segment endpoints are stitched: intersections do not create junctions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

VERSION = "drawing-graph-0.1"


def distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def center(box):
    return [(box[0]+box[2])/2, (box[1]+box[3])/2]


def area(box):
    return max(0, box[2]-box[0])*max(0, box[3]-box[1])


def inside(point, box, pad=0):
    return box[0]-pad <= point[0] <= box[2]+pad and box[1]-pad <= point[1] <= box[3]+pad


def rect_distance(point, box):
    return math.hypot(max(box[0]-point[0], point[0]-box[2], 0),
                      max(box[1]-point[1], point[1]-box[3], 0))


def point_segment(point, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0, min(1, ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy or 1)))
    q = [a[0]+t*dx, a[1]+t*dy]
    return distance(point, q), t


def norm_box(box):
    return [round(max(0, min(1, float(v))), 7) for v in box]


def vector_segments(page, kind):
    """Recover slender filled strokes as centerlines; preserve source indices."""
    w, h = page.rect.width, page.rect.height
    unit = w
    minimum = unit*(.004 if kind == "cabinet" else .006)
    result = []
    for di, drawing in enumerate(page.get_drawings()):
        lines = [(i, [p[1].x, p[1].y], [p[2].x, p[2].y])
                 for i, p in enumerate(drawing["items"]) if p[0] == "l"]
        # Some CAD exporters encode thin strokes as quadrilaterals.
        for i, item in enumerate(drawing["items"]):
            if item[0] == "qu":
                q = item[1]
                lines.extend([(i, list(q.ul), list(q.ur)), (i, list(q.ll), list(q.lr)),
                              (i, list(q.ul), list(q.ll)), (i, list(q.ur), list(q.lr))])
        candidates = [(i, a, b) for i, a, b in lines if distance(a, b) >= minimum]
        # Thin filled polygons describe a stroke, not two parallel circuits.
        if drawing["type"] in {"f", "fs"} and 2 <= len(candidates) <= 4 and len(lines) <= 5:
            longest = sorted(candidates, key=lambda s: distance(s[1], s[2]), reverse=True)
            i, a, b = longest[0]
            for _, c, d in longest[1:]:
                if distance(a, d)+distance(b, c) < distance(a, c)+distance(b, d):
                    c, d = d, c
                if max(distance(a, c), distance(b, d)) < unit*.0018:
                    candidates = [(i, [(a[0]+c[0])/2, (a[1]+c[1])/2],
                                       [(b[0]+d[0])/2, (b[1]+d[1])/2])]
                    break
        for ii, a, b in candidates:
            a, b = [a[0]/w, a[1]/h], [b[0]/w, b[1]/h]
            # Sheet frames and title/legend areas are not wire proposals.
            if not all(.02 < p[0] < .98 and .025 < p[1] < .98 for p in (a, b)):
                continue
            if all(p[0] > .55 and p[1] > .86 for p in (a,b)):
                continue
            if distance(a, b) > .88:
                continue
            result.append({"a": a, "b": b, "source": [di, ii],
                           "dash": drawing.get("dashes"), "width_pt": drawing.get("width"),
                           "paint": drawing["type"]})
    unique = {}
    for s in result:
        key = tuple(round(v, 4) for p in sorted((s["a"], s["b"])) for v in p)
        if key not in unique:
            s["id"] = f"S{len(unique)+1:05d}"
            unique[key] = s
    return list(unique.values())


def detect_boxes(page):
    import cv2
    import numpy as np
    import pymupdf
    scale = 3000/max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    mask = (gray < 245).astype(np.uint8)*255
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (.006*pix.width < w < .65*pix.width and .008*pix.height < h < .94*pix.height):
            continue
        b = [x/pix.width, y/pix.height, (x+w)/pix.width, (y+h)/pix.height]
        if (b[1] > .84 and b[0]>.55) or b[1]<.035 or b[2] > .98 or b[0] < .025:
            continue
        perimeter = cv2.arcLength(c, True)
        poly = cv2.approxPolyDP(c, .012*perimeter, True)
        if len(poly) != 4 or cv2.contourArea(c)/(w*h) < .87:
            continue
        boxes.append(norm_box(b))
    # Closed enclosures can be interrupted in a raster contour by entering wires.
    # Recover their four independent vector sides as a second proposal source.
    ss=vector_segments(page,"cabinet")
    vs=[];hs=[]
    for s in ss:
        a,b=s["a"],s["b"]
        if abs(a[0]-b[0])<.001 and abs(a[1]-b[1])>.02:
            v=[(a[0]+b[0])/2,min(a[1],b[1]),max(a[1],b[1])]
            if not any(max(abs(x-y) for x,y in zip(v,q))<.0015 for q in vs):vs.append(v)
        if abs(a[1]-b[1])<.001:
            hs.append([min(a[0],b[0]),(a[1]+b[1])/2,max(a[0],b[0])])
    for i,a in enumerate(vs):
        for b in vs[i+1:]:
            x0,x1=sorted((a[0],b[0]));y0,y1=(a[1]+b[1])/2,(a[2]+b[2])/2
            if not (.006<x1-x0<.65 and .012<y1-y0<.94 and y0>.035):continue
            if abs(a[1]-b[1])>.003 or abs(a[2]-b[2])>.003:continue
            if x0>.55 and y0>.84:continue
            if all(any(abs(y-s[1])<.003 and s[0]<=x0+.003 and s[2]>=x1-.003 for s in hs) for y in (y0,y1)):
                boxes.append(norm_box([x0,y0,x1,y1]))
    keep = []
    for b in sorted(boxes, key=area, reverse=True):
        if any(max(abs(a-z) for a, z in zip(b, q)) < .0025 for q in keep):
            continue
        keep.append(b)
    return sorted(keep, key=lambda b: (round(b[1], 2), b[0]))


def object_at(point, objects, kind):
    if kind == "route":
        choices = []
        for obj in objects:
            if obj["kind"] == "station":
                # Isotropic distances in page-width units.
                ratio = obj.get("page_ratio", 1)
                c = obj["center"]
                d = math.hypot(point[0]-c[0], (point[1]-c[1])*ratio)
                if d <= obj["radius"]+.005:
                    choices.append((d, obj))
            elif rect_distance(point, obj["bbox"]) < .004:
                choices.append((distance(point, center(obj["bbox"])), obj))
        return min(choices, key=lambda v: v[0])[1] if choices else None
    boxes = [o for o in objects if o["kind"] != "port" and rect_distance(point, o["bbox"]) < .003]
    # Smallest enclosing unit, not merely a cabinet name.
    return min(boxes, key=lambda o: area(o["bbox"])) if boxes else None


def is_border(segment, objects):
    a, b = segment["a"], segment["b"]
    for obj in objects:
        if obj["kind"] == "station":
            continue
        x0, y0, x1, y1 = obj["bbox"]
        for p, q in [([x0,y0],[x1,y0]),([x1,y0],[x1,y1]),([x1,y1],[x0,y1]),([x0,y1],[x0,y0])]:
            if point_segment(a,p,q)[0] < .0018 and point_segment(b,p,q)[0] < .0018:
                return True
    return False


def bridge_gaps(segments, objects, kind):
    """Unique facing, collinear short gaps are proposals, never confirmed wires."""
    ends=[]
    for s in segments:
        a,b=s["a"],s["b"];length=distance(a,b)
        if length<.012 or is_border(s,objects):continue
        for p,q in ((a,b),(b,a)):
            if object_at(p,objects,kind):continue
            ends.append((s,p,[(p[0]-q[0])/length,(p[1]-q[1])/length]))
    choices={}
    for i,(s,p,d) in enumerate(ends):
        options=[]
        for j,(t,q,e) in enumerate(ends):
            if i==j or s["id"]==t["id"]:continue
            gap=distance(p,q)
            if not .0015<gap<.012:continue
            v=[(q[0]-p[0])/gap,(q[1]-p[1])/gap]
            if d[0]*v[0]+d[1]*v[1]>.995 and e[0]*v[0]+e[1]*v[1]<-.995:
                options.append((gap,j))
        options.sort()
        if options and (len(options)==1 or options[1][0]-options[0][0]>.003):choices[i]=options[0][1]
    extra=[]
    for i,j in choices.items():
        if i<j and choices.get(j)==i:
            extra.append({"id":f"G{len(extra)+1:05d}","a":ends[i][1],"b":ends[j][1],
                          "source":[],"method":"collinear_gap_hypothesis","paint":"gap","dash":None,"width_pt":None})
    return segments+extra


def trace_paths(segments, objects, kind, tolerance=.0009):
    """Endpoint adjacency only. Degree>2 junctions remain unbound evidence."""
    coords, buckets, adj = [], defaultdict(list), defaultdict(list)
    def vertex(p):
        key = (math.floor(p[0]/tolerance), math.floor(p[1]/tolerance))
        near = []
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                near += [v for v in buckets[(key[0]+dx,key[1]+dy)] if distance(p,coords[v]) < tolerance]
        if near:
            return min(near, key=lambda v: distance(p,coords[v]))
        v = len(coords); coords.append(p); buckets[key].append(v)
        return v
    wires = [s for s in segments if not is_border(s, objects)]
    links = {}; vertex_pairs=set()
    for s in wires:
        u, v = vertex(s["a"]), vertex(s["b"])
        if u == v:
            continue
        pair=tuple(sorted((u,v)))
        if pair in vertex_pairs:continue
        vertex_pairs.add(pair)
        links[s["id"]] = (u,v,s)
        adj[u].append(s["id"]); adj[v].append(s["id"])
    used, paths = set(), []
    for start in list(adj):
        if len(adj[start]) == 2:
            continue
        for eid in adj[start]:
            if eid in used:
                continue
            current, edge, points, sources = start, eid, [coords[start]], []
            while edge not in used:
                used.add(edge)
                u,v,s = links[edge]; current = v if current == u else u
                points.append(coords[current]); sources.append(s["id"])
                if len(adj[current]) != 2 or object_at(coords[current], objects, kind):
                    break
                edge = next(e for e in adj[current] if e != edge)
            length = sum(distance(a,b) for a,b in zip(points,points[1:]))
            if length < .012:
                continue
            a, z = object_at(points[0],objects,kind), object_at(points[-1],objects,kind)
            paths.append({"id": f"P{len(paths)+1:05d}", "points": points, "segment_ids":sources,
                          "a_object": a["id"] if a else None, "z_object": z["id"] if z else None,
                          "branch": len(adj[start]) > 2 or len(adj[current]) > 2,
                          "method":"pdf_endpoint_chain", "length_normalized": round(length,6)})
    return paths


def build_page(page, number, requested="auto"):
    from cable_route_geometry import detect_nodes, extract_vector_segments
    boxes = detect_boxes(page)
    # The route detector is used only for route-like pages, never for port dots.
    cabinet_like = sum(area(b) > .0005 for b in boxes) >= 4
    circles = [] if requested == "cabinet" or (requested == "auto" and cabinet_like) else detect_nodes(page, extract_vector_segments(page))
    kind = requested if requested != "auto" else ("route" if len(circles) >= 3 else "cabinet")
    objects = []
    def add(kind, bbox, **extra):
        obj = {"id": f"p{number:03d}-O{len(objects)+1:04d}", "page":number, "kind":kind,
               "bbox":norm_box(bbox), "center":center(bbox), "parent_id":None,
               "name":None, "name_options":[], "name_state":"unread", "source":"geometry", **extra}
        objects.append(obj)
        return obj
    if kind == "route":
        for n in circles:
            add("station", n["bbox"], radius=n["radius_pt"]/page.rect.width,
                page_ratio=page.rect.height/page.rect.width)
        station_boxes=[b for b in boxes if .006 < b[2]-b[0] < .04 and .006 < b[3]-b[1] < .04]
        # A divided terminal rectangle is one station symbol, unlike cabinet units.
        merged=[]
        for b in station_boxes:
            mate=next((q for q in merged if abs(q[0]-b[0])<.0015 and abs(q[2]-b[2])<.0015
                       and min(abs(q[3]-b[1]),abs(b[3]-q[1]))<.0015
                       and max(q[3],b[3])-min(q[1],b[1])<.045),None)
            if mate is not None:mate[1]=min(mate[1],b[1]);mate[3]=max(mate[3],b[3])
            else:merged.append(b.copy())
        for b in merged:
            if .006 < b[2]-b[0] < .04 and .006 < b[3]-b[1] < .04 and not any(inside(center(b),o["bbox"],.005) for o in objects):
                add("station_box",b)
    else:
        for b in boxes:
            add("equipment",b)
        for o in objects:
            parents = [p for p in objects if p is not o and area(p["bbox"])>area(o["bbox"])*1.1
                       and inside([o["bbox"][0],o["bbox"][1]],p["bbox"],.001)
                       and inside([o["bbox"][2],o["bbox"][3]],p["bbox"],.001)]
            if parents:
                o["parent_id"] = min(parents,key=lambda p:area(p["bbox"]))["id"]
    segments = bridge_gaps(vector_segments(page,kind),objects,kind)
    paths = trace_paths(segments,objects,kind)
    connections = []
    ports = {}
    def endpoint(owner, point):
        if kind == "route":
            return owner
        key = (owner, round(point[0],4),round(point[1],4))
        if key not in ports:
            b = [point[0]-.0015,point[1]-.002,point[0]+.0015,point[1]+.002]
            p = add("port",b, parent_id=owner, anchor=point)
            ports[key] = p["id"]
        return ports[key]
    lookup = {o["id"]:o for o in objects}
    for path in paths:
        a,z = path["a_object"],path["z_object"]
        if not a or not z or a == z or path["branch"]:
            path["state"] = "unbound"
            continue
        # A station in the interior invalidates a bypass proposal.
        if kind == "route" and any(o["id"] not in (a,z) and any(point_segment(o["center"],u,v)[0]<.006
                 for u,v in zip(path["points"],path["points"][1:])) for o in objects):
            path["state"] = "intermediate_object"
            continue
        aa,zz = endpoint(a,path["points"][0]), endpoint(z,path["points"][-1])
        path["state"] = "bound"
        connections.append({"id":f"p{number:03d}-C{len(connections)+1:04d}","page":number,
            "a":aa,"z":zz,"path_id":path["id"],"relation":"cable" if kind=="route" else "wiring",
            "attributes":{},"attribute_evidence":[],"attribute_state":"unread","status":"candidate",
            "notes":["包含共线短断口续接候选，请检查原图"] if any(s.startswith("G") for s in path["segment_ids"]) else [],"source":"geometry"})
    return {"id":number,"kind":kind,"size_pt":[page.rect.width,page.rect.height],
            "objects":objects,"segments":segments,"paths":paths,"connections":connections}


def qualified_name(page, oid):
    lookup = {o["id"]:o for o in page["objects"]}
    parts, seen = [], set()
    while oid in lookup and oid not in seen:
        seen.add(oid); obj=lookup[oid]; parts.append(obj.get("name") or obj["id"])
        oid=obj.get("parent_id")
    return " / ".join(reversed(parts))


def export_xlsx(graph, destination):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    def safe(v):
        s = json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v
        return "'"+s if isinstance(s,str) and s[:1] in "=+-@" and s else s
    wb=Workbook(); info=wb.active;info.title="说明"
    for row in [("统一候选数据", "graph.json"),("版本",graph["version"]),("修订",graph["revision"]),
                ("复核", "使用本地复核页面修改并保存；Excel为当前数据导出快照"),
                ("输入",graph["input"]["name"]),("SHA256",graph["input"]["sha256"]),
                ("范围","未绑定路径可在图上补连；未检出不证明不存在；人工确认不由模型生成")]:info.append(row)
    ws=wb.create_sheet("连接候选")
    ws.append(["候选ID","页码","图纸类型","起点ID","起点","终点ID","终点","关系","参数","参数归属","状态","路径ID","疑点"])
    objws=wb.create_sheet("对象与端口")
    objws.append(["对象ID","页码","类型","父对象ID","名称","完整名称","名称状态","坐标","来源"])
    pathws=wb.create_sheet("路径覆盖")
    pathws.append(["页码","路径ID","状态","起点对象","终点对象","分支","折点","线段ID"])
    for p in graph["pages"]:
        for c in p["connections"]:
            ws.append(list(map(safe,[c["id"],p["id"],p["kind"],c["a"],qualified_name(p,c["a"]),c["z"],qualified_name(p,c["z"]),
                       c["relation"],c["attributes"],c["attribute_state"],c["status"],c["path_id"],"；".join(c["notes"])])))
        for o in p["objects"]:
            objws.append(list(map(safe,[o["id"],p["id"],o["kind"],o["parent_id"],o["name"],qualified_name(p,o["id"]),o["name_state"],o["bbox"],o["source"]])))
        for path in p["paths"]:
            pathws.append(list(map(safe,[p["id"],path["id"],path.get("state"),path["a_object"],path["z_object"],path["branch"],path["points"],path["segment_ids"]])))
    events=wb.create_sheet("人工修改记录");events.append(["修订","时间","修改内容"])
    for event in graph.get("events",[]):events.append([event["revision"],event["time"],safe(event["operations"])])
    for sheet in wb:
        sheet.freeze_panes="A2";sheet.auto_filter.ref=sheet.dimensions
        for c in sheet[1]:c.font=Font(bold=True,color="FFFFFF");c.fill=PatternFill("solid",fgColor="245565")
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width=min(65,max(16,max(len(str(c.value or "")) for c in col[:80])+2))
        for row in sheet:
            for c in row:c.alignment=Alignment(vertical="top",wrap_text=True)
    info.column_dimensions["B"].width=95
    wb.save(destination)


def read_graph(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path=Path(path);temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8");temp.replace(path)
