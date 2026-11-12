#!/usr/bin/env python3
"""Import externally generated candidate labels into an Electronic annotation project.

No API calls and no model loading. Import ALWAYS creates new draft regions; existing
manual regions are never changed. Model candidates are explicitly unreviewed and
cannot be exported as gold until a human changes their pending states and reviews.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "electronic-preannotation-v1"
KINDS = {"station", "equipment", "cabinet", "unit", "port", "junction", "other"}
RELATIONS = {"cable", "circuit", "fiber_allocation", "wiring", "power", "unknown"}
ROLES = {"main_diagram", "inset", "legend", "table", "notes", "mixed", "unknown"}
ATTRIBUTES = {"cable_type", "cores", "length", "construction", "rate"}
VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


def require(test: bool, message: str) -> None:
    if not test:
        raise ValueError(message)


def is_num(v: object) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def box(v: object, label: str) -> list[float]:
    require(isinstance(v, list) and len(v) == 4 and all(is_num(x) for x in v), f"{label}: bbox 需要四个数字")
    x0, y0, x1, y1 = v
    require(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, f"{label}: bbox 必须是 0~1 的有效范围")
    return list(map(float, v))


def text(v: object, label: str, maxlen: int = 300) -> str:
    require(isinstance(v, str) and 0 < len(v.strip()) <= maxlen, f"{label}: 必须是非空文字（最长 {maxlen} 字符）")
    return v.strip()


def safe_id(v: object, label: str) -> str:
    require(isinstance(v, str) and bool(VALID_ID.fullmatch(v)), f"{label}: ID 只能含英数字和 . _ -")
    return v


def in_region(target: list[float], region: list[float], label: str) -> None:
    require(target[0] >= region[0] - 1e-7 and target[1] >= region[1] - 1e-7
            and target[2] <= region[2] + 1e-7 and target[3] <= region[3] + 1e-7,
            f"{label}: 对象框超出了区域，请检查坐标系")


def page_box(v: object, region: list[float], space: str, label: str) -> list[float]:
    b = box(v, label)
    if space == "region":
        x0, y0, x1, y1 = region
        w, h = x1 - x0, y1 - y0
        b = [x0 + b[0]*w, y0 + b[1]*h, x0 + b[2]*w, y0 + b[3]*h]
    elif space != "page":
        raise ValueError(f"{label}: bbox_space 只能是 page 或 region")
    b = [round(x, 9) for x in b]
    box(b, label)
    in_region(b, region, label)
    return b


def candidate_fact(pred: object, label: str) -> dict:
    """Prefill suggested value while keeping state=pending: not a gold assertion."""
    if isinstance(pred, dict):
        require(set(pred) <= {"value", "unit", "raw"} and "value" in pred, f"{label}: 属性字典仅允许 value/unit/raw")
        value = pred["value"]
        unit = pred.get("unit", "")
        raw = pred.get("raw", "")
        require(isinstance(raw, str) and len(raw) <= 1000, f"{label}: raw 太长或无效")
    else:
        value, unit, raw = pred, "", ""
    require(value is None or isinstance(value, (str, bool)) or is_num(value), f"{label}: value 必须是标量或 null")
    require(not isinstance(value, str) or len(value) <= 300, f"{label}: 文字值过长")
    require(isinstance(unit, str) and len(unit) <= 30, f"{label}: unit 无效")
    if not raw:
        raw = "" if value is None else str(value) + (unit or "")
    result = {"state": "pending", "value": value, "raw": raw, "unit": unit,
              "evidence": [], "tolerance": 0}
    if value is not None:
        result["value_type"] = "boolean" if isinstance(value, bool) else ("number" if is_num(value) else "text")
    return result


def diagram_data(pred: dict, area: list[float], space: str, label: str) -> dict:
    require(pred.get("region_role", "main_diagram") in ROLES, f"{label}: region_role 无效")
    raw_nodes, raw_edges = pred.get("objects", []), pred.get("connections", [])
    require(isinstance(raw_nodes, list) and len(raw_nodes) <= 1000, f"{label}: objects 数量无效")
    require(isinstance(raw_edges, list) and len(raw_edges) <= 2000, f"{label}: connections 数量无效")
    nodes, mapping = [], {}
    for i, obj in enumerate(raw_nodes, 1):
        require(isinstance(obj, dict), f"{label}.objects[{i}]: 必须是对象")
        original = safe_id(obj.get("id"), f"{label}.objects[{i}].id")
        require(original not in mapping, f"{label}: 重复对象 ID {original}")
        require(obj.get("kind", "other") in KINDS, f"{label}.{original}: 对象类型无效")
        name = obj.get("name")
        require(name is None or isinstance(name, str) and len(name) <= 300, f"{label}.{original}: 名称无效")
        uid = "N-" + uuid.uuid4().hex[:12]
        mapping[original] = uid
        nodes.append({"id": uid, "bbox": page_box(obj.get("bbox"), area, space, f"{label}.{original}"),
                      "kind": obj.get("kind", "other"), "name": name or "", "name_state": "pending",
                      "parent_id": obj.get("parent_id"), "evidence": []})
    for obj in nodes:
        if obj["parent_id"] is not None:
            require(obj["parent_id"] in mapping, f"{label}: parent_id 不存在")
            obj["parent_id"] = mapping[obj["parent_id"]]
    edges, edge_ids = [], set()
    for i, edge in enumerate(raw_edges, 1):
        require(isinstance(edge, dict), f"{label}.connections[{i}]: 必须是对象")
        original = safe_id(edge.get("id"), f"{label}.connections[{i}].id")
        require(original not in edge_ids, f"{label}: 重复连接 ID {original}")
        edge_ids.add(original)
        source, target = edge.get("source"), edge.get("target")
        require(source in mapping and target in mapping and source != target,
                f"{label}.{original}: 起终点缺失、自环或未引用已定义对象")
        relation = edge.get("relation", "cable")
        require(relation in RELATIONS, f"{label}.{original}: 关系类型无效")
        directed = edge.get("directed", False)
        require(type(directed) is bool, f"{label}.{original}: directed 必须为布尔值")
        attrs = edge.get("attributes", {})
        require(isinstance(attrs, dict) and set(attrs) <= ATTRIBUTES,
                f"{label}.{original}: 不支持的属性，允许 {sorted(ATTRIBUTES)}")
        edges.append({"id": "E-" + uuid.uuid4().hex[:12], "source": mapping[source],
                      "target": mapping[target], "relation": relation, "directed": directed,
                      # Model-predicted paths/evidence are NOT promoted to human-traced truth.
                      "path": [], "attributes": {k: candidate_fact(v, f"{label}.{original}.{k}") for k, v in attrs.items()},
                      "evidence": []})
    return {"region_role": pred.get("region_role", "main_diagram"), "objects": nodes,
            "connections": edges}, len(nodes), len(edges)


def table_data(pred: dict, label: str) -> tuple[dict, int, int]:
    rows, cols, headers = pred.get("rows"), pred.get("cols"), pred.get("header_rows", 0)
    require(type(rows) is int and type(cols) is int and 1 <= rows <= 60 and 1 <= cols <= 30,
            f"{label}: 行列数必须在 1..60、1..30 内")
    require(type(headers) is int and 0 <= headers <= rows, f"{label}: 表头行数无效")
    raw = pred.get("cells", [])
    require(isinstance(raw, list) and len(raw) <= rows*cols, f"{label}: cells 数量无效")
    used, cells = set(), []
    for idx, cell in enumerate(raw, 1):
        require(isinstance(cell, dict), f"{label}.cells[{idx}]: 必须是对象")
        r, c, rs, cs = cell.get("row"), cell.get("col"), cell.get("rowspan", 1), cell.get("colspan", 1)
        require(all(type(k) is int for k in (r, c, rs, cs)) and r >= 0 and c >= 0
                and rs >= 1 and cs >= 1 and r+rs <= rows and c+cs <= cols,
                f"{label}.cells[{idx}]: 行列或合并范围错误")
        cells_used = {(a, b) for a in range(r, r+rs) for b in range(c, c+cs)}
        require(not (used & cells_used), f"{label}.cells[{idx}]: 单元格重叠")
        used |= cells_used
        t = cell.get("text", "")
        require(isinstance(t, str) and len(t) <= 5000, f"{label}.cells[{idx}]: 单元格文字无效")
        cells.append({"id": f"c{r+1}-{c+1}", "row": r, "col": c, "rowspan": rs,
                      "colspan": cs, "text": t, "state": "pending", "evidence": []})
    require(len(used) == rows*cols, f"{label}: 单元格未完全覆盖，请明确空白格与未读格")
    return {"rows": rows, "cols": cols, "header_rows": headers, "cells": cells}, 0, 0


def converted_regions(payload: object, project: dict, source_sha256: str) -> tuple[list[dict], dict]:
    """Create new unreviewed regions without touching existing project data."""
    require(isinstance(payload, dict) and payload.get("schema_version") == FORMAT,
            f"预标注文件必须是 {{schema_version: '{FORMAT}', ...}}")
    require(payload.get("document_id") == project["document"]["id"], "document_id 与标注工程不一致")
    model = text(payload.get("model"), "model", 200)
    if "source_pdf_sha256" in payload:
        require(payload["source_pdf_sha256"] == source_sha256, "源 PDF SHA256 与预标注文件不一致")
    raw_regions = payload.get("regions")
    require(isinstance(raw_regions, list) and 1 <= len(raw_regions) <= 100,
            "regions 必须是 1..100 个区域的数组")
    existing = project.get("regions", [])
    total_pages = len(project["document"]["pages"])
    imported, stats = [], {"regions": len(raw_regions), "objects": 0, "connections": 0,
                            "text_regions": 0, "table_regions": 0}
    for idx, src in enumerate(raw_regions, 1):
        label = f"regions[{idx}]"
        require(isinstance(src, dict), f"{label}: 必须是对象")
        page = src.get("page")
        require(type(page) is int and 1 <= page <= total_pages, f"{label}: PDF 页码无效")
        bbox = box(src.get("bbox"), f"{label}.bbox")
        kind = src.get("type")
        require(kind in {"diagram", "text", "table"}, f"{label}: type 只能是 diagram/text/table")
        title = text(src.get("title", f"AI预标注区域{idx}"), f"{label}.title")
        data = src.get("data")
        require(isinstance(data, dict), f"{label}: data 必须是对象")
        if kind == "diagram":
            space = src.get("bbox_space")
            require(space in {"page", "region"}, f"{label}: 图纸必须显式声明 bbox_space=page 或 region")
            result, n_nodes, n_edges = diagram_data(data, bbox, space, label)
            stats["objects"] += n_nodes
            stats["connections"] += n_edges
        elif kind == "text":
            transcript = data.get("transcript", "")
            require(isinstance(transcript, str) and len(transcript) <= 30000,
                    f"{label}: transcript 无效或过长")
            result = {"transcript": transcript, "state": "pending", "draft_source": "external_ai_preannotation"}
            stats["text_regions"] += 1
        else:
            result, _, _ = table_data(data, label)
            stats["table_regions"] += 1
        imported.append({"id": "R-AI-" + uuid.uuid4().hex[:12], "page": page,
                         "bbox": bbox, "rotation_degrees": src.get("rotation_degrees", 0),
                         "type": kind, "title": f"[AI草稿] {title}", "status": "draft",
                         "reviewer": "", "reviewed_at": None, "items": [], "contexts": [],
                         "notes": "外部模型预标注，仅供复核。请核实漏标、虚构连接、文字、框位置与参数归属；点击复核前逐项确认。",
                         "data": result,
                         "preannotation": {"model": model, "source_pdf_sha256": source_sha256,
                                           "imported_at": datetime.now(timezone.utc).isoformat(),
                                           "unverified": True}})
    require(len(existing) + len(imported) <= 2000, "导入后总区域数超过限制")
    return imported, stats


def import_file(project_dir: Path, prediction_file: Path, actor: str, dry_run: bool = False) -> dict:
    from document_annotation import AnnotationStore, validate_region
    from document_eval_core import digest, read_json
    project_dir, prediction_file = project_dir.expanduser().resolve(), prediction_file.expanduser().resolve()
    require(prediction_file.is_file() and prediction_file.stat().st_size <= 10_000_000,
            "输入 JSON 不存在或超过 10MB")
    actor = text(actor, "标注人/操作者", 120)
    store = AnnotationStore(project_dir)
    project = store.load()
    store.check_source(project)
    payload = read_json(prediction_file)
    import_hash = digest(prediction_file)
    require(not any(r.get("preannotation", {}).get("import_file_sha256") == import_hash
                    for r in project.get("regions", [])), "这个模型预标注文件已经导入过，拒绝重复添加")
    imported, summary = converted_regions(payload, project, project["document"]["sha256"])
    for region in imported:
        region["preannotation"].update(import_file_sha256=import_hash,
                                       source_filename=prediction_file.name)
        validate_region(region, len(project["document"]["pages"]), reviewed=False)
    summary.update(model=payload["model"], source=prediction_file.name, dry_run=dry_run,
                   output_project=str(project_dir))
    if not dry_run:
        store.save(project["revision"], copy.deepcopy(project["regions"]) + imported,
                   project.get("links", []), actor)
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="将外部模型 JSON 导入标注工具作为可编辑草稿；不调用任何模型API")
    p.add_argument("project", type=Path, help="标注工程目录，例如 data/annotation/D03")
    p.add_argument("--input", type=Path, required=True, help="外部模型生成并整理为约定格式的 JSON")
    p.add_argument("--actor", required=True, help="导入操作者标识，不代表人工已复核")
    p.add_argument("--dry-run", action="store_true", help="仅验证输入并打印数量，不修改工程")
    args = p.parse_args()
    try:
        summary = import_file(args.project, args.input, args.actor, args.dry_run)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not args.dry_run:
            print("导入完成：刷新 http://127.0.0.1:8766 查看 [AI草稿] 区域，逐项复核后才可导出真值。")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())