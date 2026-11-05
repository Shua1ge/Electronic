"""Conservative circular-station and direct VECTOR-segment proposals for cable maps.

This is *not* a general topology solver. No labels are inferred from circles or
nearby cable annotations. Missing an edge is evidence of a detector limitation,
not proof that the stations are disconnected. All resulting records need review.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ring_fraction(gray, x: float, y: float, radius: int) -> float:
    import numpy as np
    angles = np.linspace(0.0, math.tau, 144, endpoint=False)
    xx = np.clip(np.rint(x + radius * np.cos(angles)).astype(int), 0, gray.shape[1] - 1)
    yy = np.clip(np.rint(y + radius * np.sin(angles)).astype(int), 0, gray.shape[0] - 1)
    return float((gray[yy, xx] < 245).mean())


def _circle_appearance(gray, x: float, y: float, r: float) -> tuple[bool, float]:
    """Center must be blank; look for separated circular strokes, not text glyphs."""
    import numpy as np
    xs = np.arange(max(0, int(x - 5)), min(gray.shape[1], int(x + 6)))
    ys = np.arange(max(0, int(y - 5)), min(gray.shape[0], int(y + 6)))
    xx, yy = np.meshgrid(xs, ys)
    disk = gray[np.ix_(ys, xs)][(xx-x)**2 + (yy-y)**2 <= 25]
    if disk.size == 0 or float((disk < 90).mean()) > .075:
        return False, 0.0
    inner = max((_ring_fraction(gray, x, y, k) for k in range(7, 10)), default=0.0)
    outer = max((_ring_fraction(gray, x, y, k) for k in range(11, 14)), default=0.0)
    # Most engineering station glyphs are concentric rings. Larger or heavier
    # terminals can be less regular; admit them only with endpoint support.
    score = min(inner, outer)
    return score >= .25, round(score, 3)


def extract_vector_segments(page) -> list[dict[str, Any]]:
    """Extract long straight strokes from a PDF with glyph outlines as vector paths.

    Filled paths may outline one thick cable with multiple near-identical edges;
    edge records are deduplicated later. Length filter rejects most letter contours.
    """
    w, h = page.rect.width, page.rect.height
    found = []
    for drawing in page.get_drawings():
        for part in drawing["items"]:
            if part[0] != "l":
                continue
            a, b = part[1:3]
            length = _distance((a.x, a.y), (b.x, b.y))
            if not (max(14, w*.015) <= length <= w*.42):
                continue
            if not (.025*w < a.x < .975*w and .025*w < b.x < .975*w
                    and .025*h < a.y < .89*h and .025*h < b.y < .89*h):
                continue
            found.append({"a": [round(a.x, 2), round(a.y, 2)],
                          "b": [round(b.x, 2), round(b.y, 2)],
                          "length_pt": round(length, 2), "type": drawing["type"]})
    # Many filled strokes are duplicated exact/near-exact by PDF outlines.
    uniq = {}
    for s in found:
        a, b = sorted((s["a"], s["b"]))
        key = tuple(round(v, 1) for v in (*a, *b))
        if key not in uniq:
            uniq[key] = s
    return list(uniq.values())


def detect_nodes(page, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raster Hough proposals, validated by concentric rings and vector endpoints.

    Image processing is at a fixed 2 pixels per PDF point for stable node IDs.
    Empty returns are valid (unsupported symbol design / raster-only drawing).
    """
    import cv2
    import numpy as np
    pix = page.get_pixmap(matrix=__import__("pymupdf").Matrix(2, 2),
                          colorspace=__import__("pymupdf").csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width))
    raw = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.15, minDist=34,
                           param1=120, param2=21, minRadius=7, maxRadius=18)
    if raw is None:
        return []
    proposals = []
    w, h = page.rect.width, page.rect.height
    for xx, yy, radius in raw[0]:
        x, y, r = float(xx/2), float(yy/2), float(radius/2)
        if not (.045*w <= x <= .96*w and .035*h <= y <= .835*h):
            continue
        looks_round, score = _circle_appearance(gray, float(xx), float(yy), float(radius))
        if not looks_round:
            continue
        # A genuine symbol should be at the endpoint of a long enough path.
        # Require enough geometric evidence to reject glyphs in captions.
        reaches = 0
        for s in segments:
            for end in (s["a"], s["b"]):
                if r*.55 <= _distance((x,y), end) <= r+5.0:
                    reaches += 1
                    break
        if reaches < 1:
            continue
        proposals.append({"x_pt": round(x, 2), "y_pt": round(y, 2),
                          "radius_pt": round(r, 2), "circle_score": score,
                          "endpoint_support": reaches})
    # The same symbol can be proposed at slightly different centers, only keep
    # the best supported detection; not a station-name lookup.
    proposals.sort(key=lambda n: (-n["endpoint_support"], -n["circle_score"]))
    dedup = []
    for n in proposals:
        if not any(_distance((n["x_pt"], n["y_pt"]),
                             (q["x_pt"], q["y_pt"])) < 9 for q in dedup):
            dedup.append(n)
    dedup.sort(key=lambda n: (round(n["y_pt"] / 4), n["x_pt"]))
    for idx, n in enumerate(dedup, 1):
        n["id"] = f"N{idx:03d}"
        n["bbox"] = [round((n["x_pt"]-n["radius_pt"])/w, 6),
                     round((n["y_pt"]-n["radius_pt"])/h, 6),
                     round((n["x_pt"]+n["radius_pt"])/w, 6),
                     round((n["y_pt"]+n["radius_pt"])/h, 6)]
    return dedup


def direct_edges(nodes: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Direct strokes ending on two circles only; NOT arbitrary line intersections.

    We do not close breaks, infer turns, or connect crossing strokes. Unmatched
    vector strokes are left for review. This is a deliberately low-recall pass.
    """
    groups = defaultdict(list)
    for idx, s in enumerate(segments):
        matched = []
        for pt in (s["a"], s["b"]):
            choices = []
            for n in nodes:
                d = _distance(pt, (n["x_pt"], n["y_pt"]))
                if n["radius_pt"]*.45 <= d <= n["radius_pt"] + 5:
                    choices.append((d, n["id"]))
            matched.append(min(choices) if choices else None)
        if not all(matched) or matched[0][1] == matched[1][1]:
            continue
        a, b = sorted((matched[0][1], matched[1][1]))
        # If an actual detected station symbol lies in the interior of this
        # vector segment, A--B is not a direct physical edge.
        ax, ay = s["a"]
        bx, by = s["b"]
        dd = (bx-ax)**2 + (by-ay)**2
        blocked = False
        if dd > 1e-6:
            for n in nodes:
                if n["id"] in (a, b):
                    continue
                t = ((n["x_pt"]-ax)*(bx-ax) + (n["y_pt"]-ay)*(by-ay))/dd
                if .04 < t < .96:
                    closest = (ax+t*(bx-ax), ay+t*(by-ay))
                    if _distance(closest, (n["x_pt"], n["y_pt"])) < n["radius_pt"] + 1:
                        blocked = True
                        break
        if not blocked:
            groups[(a, b)].append(idx)
    out = []
    for (a, b), indices in sorted(groups.items()):
        out.append({"id": f"E{len(out)+1:03d}", "a_node": a, "b_node": b,
                    "method": "single_direct_pdf_vector_segment",
                    "support_segments": len(indices), "segment_ids": indices[:15]})
    return out


def _nearest_ocr_hint(node: dict[str, Any], lines: list[dict[str, Any]], w: float, h: float) -> str:
    x, y = node["x_pt"], node["y_pt"]
    near = []
    for line in lines:
        b = line.get("bbox")
        if not b or not isinstance(line.get("text"), str):
            continue
        center = (((b[0]+b[2])/2)*w, ((b[1]+b[3])/2)*h)
        # A station name usually appears beside / just below its circle.
        dist = _distance((x,y), center)
        if 6 <= dist <= 34:
            near.append((dist, line["text"].strip()))
    return min(near)[1] if near else ""


def read_station_map(path: Path | None, pages: list[dict[str, Any]]) -> dict[tuple[int,str], str]:
    """Only operator-supplied labels can be treated as grounded station identity."""
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"站点标注表不存在：{path}")
    eligible = {(p["page"], n["id"]) for p in pages for n in p["nodes"]}
    data = {}
    seen_names = set()
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not {"页码", "节点ID", "确认站名"}.issubset(reader.fieldnames or []):
            raise ValueError("--station-map CSV 需要包含 页码、节点ID、确认站名 三列")
        for row in reader:
            name = row["确认站名"].strip()
            if not name:
                continue
            try:
                key = (int(row["页码"]), row["节点ID"].strip())
            except ValueError as ex:
                raise ValueError("站点标注表页码无效") from ex
            if key not in eligible:
                raise ValueError(f"站点标注表节点不存在：{key}")
            normalized = "".join(name.split()).casefold()
            if key in data or (key[0], normalized) in seen_names:
                raise ValueError(f"同页站点标注存在重复：{name}")
            data[key] = name
            seen_names.add((key[0], normalized))
    return data


def build_geometry(pdf: Path, out: Path, ocr: dict[str, Any],
                   station_map: Path | None = None) -> dict[str, Any]:
    import cv2
    import pymupdf
    from PIL import Image

    dest = out / "geometry"
    dest.mkdir(exist_ok=True)
    results = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, 1):
            segments = extract_vector_segments(page)
            nodes = detect_nodes(page, segments)
            edges = direct_edges(nodes, segments)
            for n in nodes:
                n["ocr_hint"] = _nearest_ocr_hint(n, ocr["pages"].get(str(i), []),
                                                   page.rect.width, page.rect.height)
            results.append({"page": i, "width_pt": round(page.rect.width,2),
                            "height_pt": round(page.rect.height,2), "nodes": nodes,
                            "edges": edges, "candidate_vector_segments": len(segments),
                            "notes": ["检测仅覆盖圆形端点、直接PDF矢量线段；折线、多段标签遮挡和纯扫描线路可能漏检",
                                      "线路交叉不创建连接；缺少线段不是不连通的证明"]})
    manual = read_station_map(station_map, results)
    with (dest / "station_anchors.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["页码", "节点ID", "X归一化", "Y归一化", "OCR附近文本(仅提示)", "确认站名"])
        for p in results:
            for n in p["nodes"]:
                n["confirmed_name"] = manual.get((p["page"], n["id"]))
                writer.writerow([p["page"], n["id"], round(n["x_pt"]/p["width_pt"],6),
                                round(n["y_pt"]/p["height_pt"],6), n["ocr_hint"],
                                n["confirmed_name"] or ""])
    for p in results:
        page_no = p["page"]
        img_path = out / "pages" / f"page{page_no:03d}.png"
        image = cv2.imread(str(img_path))
        if image is None:
            continue
        fx = image.shape[1] / p["width_pt"]
        fy = image.shape[0] / p["height_pt"]
        lookup = {n["id"]:n for n in p["nodes"]}
        for e in p["edges"]:
            a, b = lookup[e["a_node"]], lookup[e["b_node"]]
            cv2.line(image,(round(a["x_pt"]*fx),round(a["y_pt"]*fy)),
                     (round(b["x_pt"]*fx),round(b["y_pt"]*fy)),(0,170,0),2)
        for n in p["nodes"]:
            x, y = round(n["x_pt"]*fx), round(n["y_pt"]*fy)
            cv2.circle(image,(x,y),max(5,round(n["radius_pt"]*fx)),(0,80,255),2)
            cv2.putText(image,n["id"],(x+8,y-6),cv2.FONT_HERSHEY_SIMPLEX,.48,
                        (40,30,210),1,cv2.LINE_AA)
        cv2.imwrite(str(dest / f"page{page_no:03d}_overlay.png"), image)
        # Avoid embedding 100k vector path primitives into the JSON report.
        (dest / f"page{page_no:03d}.json").write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding="utf-8")
    summary = {"method": "circular_terminal_Hough_and_direct_PDF_vectors",
               "pages": results, "manual_station_map": bool(station_map),
               "warning": "没有几何确认的路径不能推断为不存在；有线条也不等于长度/类型标注归属正确"}
    (dest / "geometry.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


def check_candidate(record: dict[str, Any], geometry: dict[str, Any] | None) -> dict[str, Any]:
    """Match only *confirmed* station labels, never a model's guessed coordinates."""
    if geometry is None:
        return {"state":"未启用", "nodes":[], "edge_id":None}
    page = next((p for p in geometry["pages"] if p["page"]==record["page"]),None)
    if page is None or not page["nodes"]:
        return {"state":"未检测到可用圆形站点", "nodes":[], "edge_id":None}
    mapping = {}
    for n in page["nodes"]:
        name = n.get("confirmed_name")
        if name:
            mapping["".join(name.split()).casefold()] = n["id"]
    first = mapping.get("".join((record.get("start") or "").split()).casefold())
    second = mapping.get("".join((record.get("end") or "").split()).casefold())
    if not first or not second:
        return {"state":"端点尚未标注到圆形节点", "nodes":list(filter(None,[first,second])), "edge_id":None}
    direct = next((e for e in page["edges"] if {e["a_node"],e["b_node"]}=={first,second}),None)
    if direct:
        return {"state":"检测到直连矢量线段（参数归属未核验）",
                "nodes":[first,second], "edge_id": direct["id"]}
    return {"state":"未检出两节点的直接矢量线段（需复核）",
            "nodes":[first,second],"edge_id":None}