#!/usr/bin/env python3
"""Local PDF annotation project, validation and gold export. No model dependency."""
from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from document_eval_core import (ATTRIBUTES, RELATIONS, ROLES, STATES, box_valid, digest,
                                identifier, json_hash, number, read_json, require,
                                text, validate_fact, validate_manifest, write_json)

FACT_STATES = STATES | {"pending"}
KINDS = {"station", "equipment", "cabinet", "unit", "port", "junction", "other"}


def now():
    return datetime.now(timezone.utc).isoformat()


def pending():
    return {"state": "pending", "value": None, "raw": "", "unit": "", "evidence": [], "tolerance": 0}


def new_region(rid, page, bbox, kind):
    base = {"id": rid, "page": page, "bbox": bbox, "type": kind, "title": "",
            "status": "draft", "reviewer": "", "reviewed_at": None,
            "items": [], "contexts": [], "notes": ""}
    if kind == "text":
        base["data"] = {"transcript": "", "state": "pending", "draft_source": None}
    elif kind == "table":
        base["data"] = make_table(3, 3)
    else:
        base["data"] = {"region_role": "main_diagram", "objects": [], "connections": []}
    return base


def make_table(rows, cols, values=None):
    return {"rows": rows, "cols": cols, "header_rows": 1,
            "cells": [{"id": f"c{r+1}-{c+1}", "row": r, "col": c, "rowspan": 1, "colspan": 1,
                       "text": values[r][c] if values else "", "state": "visible" if values else "pending",
                       "evidence": []} for r in range(rows) for c in range(cols)]}


def validate_evidence(evidence, page_count):
    require(isinstance(evidence, list) and len(evidence) <= 30, "证据必须为数组且不超过30项")
    for e in evidence:
        require(type(e.get("page")) is int and 1 <= e["page"] <= page_count and box_valid(e.get("bbox")), "证据页码或位置无效")


def validate_table(table, allow_pending=False):
    rows, cols = table.get("rows"), table.get("cols")
    require(type(rows) is int and type(cols) is int and 1 <= rows <= 60 and 1 <= cols <= 30, "表格限制为1—60行、1—30列")
    require(type(table.get("header_rows")) is int and 0 <= table["header_rows"] <= rows, "表头行数无效")
    require(isinstance(table.get("cells"), list), "单元格数组缺失")
    occupied, ids = set(), set()
    for cell in table["cells"]:
        require(identifier(cell.get("id")) and cell["id"] not in ids, "单元格ID重复或无效")
        ids.add(cell["id"])
        r, c, rs, cs = (cell.get(k) for k in ("row", "col", "rowspan", "colspan"))
        require(all(type(v) is int for v in (r, c, rs, cs)) and r >= 0 and c >= 0 and rs > 0 and cs > 0
                and r+rs <= rows and c+cs <= cols, "单元格范围无效")
        slots = {(y, x) for y in range(r, r+rs) for x in range(c, c+cs)}
        require(not occupied & slots, "合并单元格相互重叠")
        occupied |= slots
        allowed = FACT_STATES if allow_pending else STATES
        require(cell.get("state") in allowed and isinstance(cell.get("text"), str), "单元格状态/文本无效")
    require(len(occupied) == rows*cols, "表格存在未覆盖的单元格；拆分合并后请补齐空格")


def validate_region(region, page_count, reviewed=False):
    require(identifier(region.get("id")), "区域ID无效")
    require(type(region.get("page")) is int and 1 <= region["page"] <= page_count, "区域页码无效")
    require(box_valid(region.get("bbox")), "区域坐标无效")
    require(type(region.get("rotation_degrees", 0)) is int and region.get("rotation_degrees", 0) in {0, 90, 180, 270}, "额外旋转角度无效")
    require(region.get("type") in {"text", "table", "diagram"}, "区域类型无效")
    require(region.get("status") in {"draft", "reviewed"}, "区域审核状态无效")
    require(isinstance(region.get("title"), str) and isinstance(region.get("notes"), str), "标题/备注无效")
    data = region.get("data")
    require(isinstance(data, dict), "区域内容缺失")
    if region["type"] == "text":
        require(isinstance(data.get("transcript"), str) and data.get("state") in FACT_STATES, "原文/状态无效")
        if reviewed:
            require(data["state"] != "pending", "正文尚未标注")
    elif region["type"] == "table":
        validate_table(data, allow_pending=not reviewed)
        for cell in data["cells"]:
            validate_evidence(cell.get("evidence", []), page_count)
    else:
        require(data.get("region_role") in ROLES, "图示区域角色无效")
        if reviewed:
            require(data["region_role"] != "unknown", "请先确认图示区域角色")
        require(isinstance(data.get("objects"), list) and isinstance(data.get("connections"), list), "图示数组缺失")
        ids = set()
        for obj in data["objects"]:
            require(identifier(obj.get("id")) and obj["id"] not in ids, "对象ID重复或无效")
            ids.add(obj["id"])
            require(box_valid(obj.get("bbox")) and obj.get("kind") in KINDS, "对象位置/类型无效")
            b, target = obj["bbox"], region["bbox"]
            require(b[0] >= target[0] and b[1] >= target[1] and b[2] <= target[2] and b[3] <= target[3], "对象必须位于所属区域内")
            require(isinstance(obj.get("name"), str) and obj.get("name_state") in FACT_STATES, "对象名称/状态无效")
            if reviewed:
                require(obj["name_state"] != "pending", "有对象名称尚未处理")
                require(obj["name_state"] != "visible" or text(obj["name"]), "可判定对象名称不能为空")
            validate_evidence(obj.get("evidence", []), page_count)
        parents = {o["id"]: o.get("parent_id") for o in data["objects"]}
        for key, parent in parents.items():
            seen = {key}
            while parent:
                require(parent in parents and parent not in seen, "所属对象不存在或形成循环")
                seen.add(parent)
                parent = parents[parent]
        edge_ids = set()
        for edge in data["connections"]:
            require(identifier(edge.get("id")) and edge["id"] not in edge_ids, "连接ID重复或无效")
            edge_ids.add(edge["id"])
            require(edge.get("source") in ids and edge.get("target") in ids and edge["source"] != edge["target"], "连接需要两个不同的已标注端点")
            require(edge.get("relation") in RELATIONS and type(edge.get("directed")) is bool, "连接类型/方向无效")
            if reviewed:
                require(edge["relation"] != "unknown", "无法确定的连接请记录为待裁决判断项，不要加入已知真值连接")
            points = edge.get("path", [])
            require(isinstance(points, list) and all(isinstance(p, list) and len(p) == 2 and all(number(x) and 0 <= x <= 1 for x in p) for p in points), "线路路径点无效")
            require(isinstance(edge.get("attributes"), dict) and set(edge["attributes"]) <= ATTRIBUTES, "属性字段无效")
            validate_evidence(edge.get("evidence", []), page_count)
            for field, spec in edge["attributes"].items():
                validate_annotation_fact(spec, page_count, reviewed)
    items = region.get("items")
    require(isinstance(items, list), "判断项数组缺失")
    ids = set()
    for item in items:
        require(identifier(item.get("id")) and item["id"] not in ids and text(item.get("question")), "判断项ID/问题无效")
        ids.add(item["id"])
        validate_annotation_fact(item, page_count, reviewed)
    require(isinstance(region.get("contexts"), list) and len(region["contexts"]) <= 2, "最多关联两个额外上下文区域")
    for context in region["contexts"]:
        require(identifier(context.get("region_id")) and text(context.get("purpose")), "上下文区域和适用说明必填")
    if reviewed:
        require(text(region.get("reviewer")), "请填写复核人标识")


def validate_annotation_fact(spec, page_count, reviewed):
    require(spec.get("state") in FACT_STATES and isinstance(spec.get("raw", ""), str)
            and isinstance(spec.get("unit", ""), str), "字段状态/原文/单位无效")
    validate_evidence(spec.get("evidence", []), page_count)
    if spec["state"] != "pending" and (reviewed or spec["state"] != "visible" or spec.get("value") is not None):
        validate_fact(spec)
    else:
        require(not reviewed, "有字段/判断项尚未标注，可先保存草稿")
    if spec["state"] == "visible" and spec.get("unit") and (reviewed or spec.get("value") is not None):
        require(number(spec.get("value")), "带单位字段必须填写数字确认值")


def project_init(pdf, output, document_id="D01"):
    import pymupdf
    pdf, output = Path(pdf).expanduser().resolve(), Path(output).expanduser().resolve()
    require(identifier(document_id), "文档ID无效")
    require(pdf.is_file(), "PDF不存在")
    with pymupdf.open(pdf) as doc:
        require(not doc.needs_pass, "请先在本机解密PDF")
        pages = [{"page": i+1, "width": p.rect.width, "height": p.rect.height,
                  "rotation": p.rotation, "native_characters": len(p.get_text().strip())} for i, p in enumerate(doc)]
    output.mkdir(parents=True, exist_ok=False)
    (output / "pages").mkdir()
    shutil.copyfile(pdf, output / "source.pdf")
    project = {"schema_version": "annotation-1.0", "revision": 0, "created_at": now(),
               "document": {"id": document_id, "name": pdf.name, "sha256": digest(output / "source.pdf"),
                            "project_group_id": None, "source_group_id": None, "pages": pages},
               "regions": [], "links": [], "events": []}
    write_json(output / "project.json", project)
    return output


class AnnotationConflict(ValueError):
    pass


class AnnotationStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.lock = threading.RLock()
        self.load()

    def load(self):
        project = read_json(self.root / "project.json")
        require(project.get("schema_version") == "annotation-1.0", "不是受支持的标注工程")
        return project

    def check_source(self, project):
        require(digest(self.root / "source.pdf") == project["document"]["sha256"], "源PDF已变化，请新建工程")

    def save(self, revision, regions, links, actor):
        with self.lock:
            old = self.load()
            if old["revision"] != revision:
                raise AnnotationConflict("另一个窗口已保存新版本，请刷新后重新编辑")
            require(text(actor) and len(actor) <= 120, "请填写标注人标识")
            require(isinstance(regions, list) and len(regions) <= 2000, "区域数组无效")
            ids = set()
            before = {r["id"]: r for r in old["regions"]}
            regions = copy.deepcopy(regions)
            for region in regions:
                require(region["id"] not in ids, "区域ID重复")
                ids.add(region["id"])
                previous = before.get(region["id"])
                # Any content edit reopens review, even if a stale client retains reviewed.
                if previous and previous["status"] == "reviewed":
                    keys = {k: v for k, v in region.items() if k not in {"status", "reviewer", "reviewed_at"}}
                    old_keys = {k: v for k, v in previous.items() if k not in {"status", "reviewer", "reviewed_at"}}
                    if json_hash(keys) != json_hash(old_keys):
                        region.update(status="draft", reviewer="", reviewed_at=None)
                validate_region(region, len(old["document"]["pages"]), reviewed=region["status"] == "reviewed")
                if region["status"] == "reviewed":
                    region["reviewed_at"] = region.get("reviewed_at") or now()
            for region in regions:
                require(all(c["region_id"] in ids and c["region_id"] != region["id"] for c in region["contexts"]), "上下文区域不存在或引用自身")
            content_keys = lambda r: {k: v for k, v in r.items() if k not in {"status", "reviewer", "reviewed_at"}}
            changed_ids = {r["id"] for r in regions if r["id"] in before
                           and json_hash(content_keys(r)) != json_hash(content_keys(before[r["id"]]))}
            for region in regions:
                if any(c["region_id"] in changed_ids for c in region["contexts"]):
                    region.update(status="draft", reviewer="", reviewed_at=None)
            require(isinstance(links, list), "关联记录无效")
            for link in links:
                require(link.get("source") in ids and link.get("target") in ids and link["source"] != link["target"], "关联区域不存在")
                require(link.get("relation") in {"explains", "same_entity", "conflicts", "applies_to"}, "关联类型无效")
            old.update(revision=old["revision"]+1, regions=regions, links=copy.deepcopy(links))
            old["events"].append({"revision": old["revision"], "at": now(), "actor": actor,
                                  "regions_sha256": json_hash(regions), "links_sha256": json_hash(links)})
            # Keep complete revision snapshots outside the editable current file.
            history = self.root / "history"
            history.mkdir(exist_ok=True)
            write_json(history / f"revision-{old['revision']:06d}.json", old)
            write_json(self.root / "project.json", old)
            return old

    def page_image(self, page_number):
        from document_eval import render_image
        import pymupdf
        with self.lock:
            project = self.load()
            require(1 <= page_number <= len(project["document"]["pages"]), "页码不存在")
            path = self.root / "pages" / f"page-{page_number:03d}.png"
            if not path.is_file():
                with pymupdf.open(self.root / "source.pdf") as doc:
                    render_image(doc[page_number-1], [0, 0, 1, 1], path, 8000000, 2600)
            return path

    def suggest_text(self, region_id, language="chi_sim+eng"):
        import pymupdf
        project = self.load()
        region = next((r for r in project["regions"] if r["id"] == region_id), None)
        require(region is not None, "请先保存区域")
        with pymupdf.open(self.root / "source.pdf") as doc:
            page = doc[region["page"]-1]
            w, h = page.rect.width, page.rect.height
            selected = []
            for word in page.get_text("words"):
                b = pymupdf.Rect(word[:4]) * page.rotation_matrix
                x, y = (b.x0+b.x1)/2/w, (b.y0+b.y1)/2/h
                target = region["bbox"]
                if target[0] <= x <= target[2] and target[1] <= y <= target[3]:
                    selected.append((word[5], word[6], word[7], word[4]))
        if selected:
            lines = {}
            for block, line, index, value in sorted(selected):
                lines.setdefault((block, line), []).append(value)
            return {"text": "\n".join(" ".join(v) for v in lines.values()), "source": "native_text_draft"}
        require(shutil.which("tesseract"), "该区域无可用文字层；可直接录入，或安装 tesseract-ocr 与 tesseract-ocr-chi-sim 后重试")
        from PIL import Image
        page_path = self.page_image(region["page"])
        with Image.open(page_path) as image:
            b = region["bbox"]
            cropped = image.crop((int(b[0]*image.width), int(b[1]*image.height), math.ceil(b[2]*image.width), math.ceil(b[3]*image.height)))
            import io
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
        process = subprocess.run(["tesseract", "stdin", "stdout", "-l", language, "--psm", "6"],
                                 input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        require(process.returncode == 0, process.stderr.decode(errors="replace")[-1000:])
        return {"text": process.stdout.decode("utf-8").strip(), "source": "tesseract_draft"}


def fact_export(spec):
    value = spec.get("value") if spec["state"] == "visible" else None
    if value is not None and spec.get("unit"):
        value = {"value": value, "unit": spec["unit"]}
    return {"state": spec["state"], "value": value, "tolerance": spec.get("tolerance", 0)}


def export_project(root, output, condition="A"):
    store = AnnotationStore(root)
    with store.lock:
        project = store.load()
        store.check_source(project)
    require(project["regions"], "请先框选至少一个区域")
    output = Path(output).expanduser().resolve()
    require(not output.exists(), "导出目录已存在，请使用新目录")
    by_id = {r["id"]: r for r in project["regions"]}
    document = project["document"]
    cases, gold_cases = [], {}
    for region in project["regions"]:
        cid = f"{document['id']}-{region['id']}-{condition}"
        contexts = []
        if condition in {"B", "C"}:
            contexts.append({"page": region["page"], "bbox": [0, 0, 1, 1], "rotation_degrees": region.get("rotation_degrees", 0), "purpose": "same-page visual context"})
        if condition == "C":
            require(region["contexts"], f"{region['id']}：C条件需人工选择适用的上下文区域")
            for context in region["contexts"]:
                ref = by_id[context["region_id"]]
                contexts.append({"page": ref["page"], "bbox": ref["bbox"], "rotation_degrees": ref.get("rotation_degrees", 0), "purpose": context["purpose"]})
        # No transcript, annotation title, attribute, answer, or cross-region gold link enters this manifest.
        case = {"id": cid, "document_id": document["id"], "page": region["page"], "region_id": region["id"],
                "target_bbox": region["bbox"], "rotation_degrees": region.get("rotation_degrees", 0), "task_type": region["type"], "condition": condition,
                "contexts": contexts, "items": [{"id": x["id"], "question": x["question"]} for x in region["items"]]}
        if condition == "C":
            case["context_selection"] = "oracle_diagnostic"
        cases.append(case)
        if region["status"] != "reviewed":
            gold_cases[cid] = None
            continue
        validate_region(region, len(document["pages"]), reviewed=True)
        data = region["data"]
        g = {"task_type": region["type"], "complete": True, "items": {x["id"]: fact_export(x) for x in region["items"]}}
        if region["type"] == "text":
            g.update(transcript=data["transcript"], state=data["state"])
        elif region["type"] == "table":
            g.update(copy.deepcopy(data))
        else:
            g.update(region_role=data["region_role"], objects=[], connections=[])
            for obj in data["objects"]:
                g["objects"].append({"id": obj["id"], "bbox": obj["bbox"], "kind": obj["kind"],
                                     "name": obj["name"] if obj["name_state"] == "visible" else None,
                                     "parent_id": obj.get("parent_id")})
            for edge in data["connections"]:
                g["connections"].append({k: copy.deepcopy(edge[k]) for k in ("id", "source", "target", "relation", "directed")})
                g["connections"][-1]["attributes"] = {key: fact_export(value) for key, value in edge["attributes"].items()}
        gold_cases[cid] = g
    manifest = {"schema_version": "1.0", "study_role": "development", "documents": [{"id": document["id"],
                "path": str(store.root / "source.pdf"), "sha256": document["sha256"],
                "project_group_id": document["project_group_id"], "source_group_id": document["source_group_id"]}], "cases": cases}
    validate_manifest(manifest)
    gold = {"schema_version": "2.0", "manifest_sha256": json_hash(manifest),
            "annotation_status": "reviewed" if any(v is not None for v in gold_cases.values()) else "pending",
            "reviewer": ",".join(sorted({r["reviewer"] for r in project["regions"] if r["status"] == "reviewed"})),
            "annotation_revision": project["revision"], "cases": gold_cases}
    output.mkdir(parents=True)
    write_json(output / "manifest.json", manifest)
    write_json(output / "gold.json", gold)
    write_json(output / "annotation_snapshot.json", project)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("pdf", type=Path)
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--document-id", default="D01")
    serve = commands.add_parser("serve")
    serve.add_argument("project", type=Path)
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--ocr-lang", default="chi_sim+eng")
    export = commands.add_parser("export")
    export.add_argument("project", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--condition", choices=["A", "B", "C"], default="A")
    args = p.parse_args()
    if args.command == "init":
        print(project_init(args.pdf, args.output, args.document_id))
    elif args.command == "serve":
        from document_annotation_server import serve_project
        serve_project(args.project, args.port, args.ocr_lang)
    else:
        print(export_project(args.project, args.output, args.condition))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
