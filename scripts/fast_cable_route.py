#!/usr/bin/env python3
"""Ubuntu/CUDA Fast prototype: PDF cable-route candidates, never approved facts.

Standalone by design. The macOS/MLX Audit pipeline is left unchanged.
See docs/fast-cable-route-ubuntu.md before processing actual engineering data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

VERSION = "fast-cable-route-0.3"
MODEL_DEFAULT = "Qwen/Qwen3.5-2B"
PROMPT = """你正在读取电力通信工程《光缆路由图》的一块局部图像。
只提取两个站点都在本块图内、可以沿可见线条追踪至圆形端点的直接光缆。不要连接跨越图块边界的线路。
先逐字读取端点站名，再读取同一线条上的光缆标注。线路交叉并不表示有节点；不得将相邻线路的标签挪过来。
可见文字不等于已核实连接；你提出的是待人工确认的候选。看不清的字段用null，不用常识猜测。保留工程原名，不改写为熟悉站名。
只写JSON对象，不要Markdown或解释。结构如下：
{"站点":[],"光缆连接":[{"起点":"站名","终点":"站名","类型":"OPGW","芯数":"24芯","长度km":"11.3km","回路数":null,"建设状态":null}],"待核对项":[]}
上述只是数据形状，不是图中的事实，不要照抄示例。每块最多3条能看清的连接，没有就返回空数组。
建设状态只有明确依据本页图例区分时才填写；细线图例的“已有光缆或其他工程待建光缆”不得简化为已建。
"""
HEADERS = [
    "候选ID", "页码", "起点", "终点", "光缆类型", "芯数", "长度(km)", "回路数",
    "建设状态", "核对状态", "证据检查", "疑点/原因", "OCR支持", "来源切片", "人工备注",
    "几何检查", "圆形端点ID", "几何路径ID",
]
STATUSES = ("待核对", "已确认", "已纠正", "拒绝")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fd:
        for part in iter(lambda: fd.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def normalize_text(value: Any) -> str:
    return re.sub(r"[\s，,。.:：\-_/（()）]+", "", str(value or "")).lower()


def text_or_none(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    raw = str(value).strip()
    return raw if raw and raw.lower() not in {"null", "none", "未知", "不清楚", "无"} else None


def parse_cores(value: Any) -> tuple[int | None, str | None]:
    if value is None or str(value).strip().lower() in {"", "null", "none"}:
        return None, None
    if isinstance(value, bool):
        return None, "芯数格式错误"
    raw = str(value).strip().replace(" ", "")
    m = re.fullmatch(r"(\d{1,4})(?:芯)?", raw)
    if not m or int(m[1]) == 0:
        return None, "芯数格式错误"
    return int(m[1]), None


def parse_length(value: Any) -> tuple[float | None, str | None]:
    if value is None or str(value).strip().lower() in {"", "null", "none"}:
        return None, None
    if isinstance(value, bool):
        return None, "长度格式错误"
    raw = str(value).strip().replace(" ", "").replace("ＫＭ", "km").replace("KM", "km")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:km|千米)?", raw, re.I)
    if not m:
        return None, "长度格式错误"
    n = float(m[1])
    return (round(n, 5), None) if math.isfinite(n) and n > 0 else (None, "长度必须为正数")


def parse_reply(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Do not 'repair' model text by guessing missing objects or values."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, count=1, flags=re.I)
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()
    try:
        data = json.loads(s)
    except (ValueError, TypeError) as ex:
        return None, f"JSON 无法解析（可能被截断）：{str(ex)[:120]}"
    if not isinstance(data, dict) or not isinstance(data.get("光缆连接"), list):
        return None, "输出不是规定的包含光缆连接数组的 JSON 对象"
    return data, None


def grid_boxes(cols: int, rows: int, overlap: float) -> list[tuple[float, float, float, float]]:
    """Deterministic normalized boxes, with overlap but full-page coverage."""
    if not (1 <= cols <= 8 and 1 <= rows <= 8 and 0 <= overlap <= .25):
        raise ValueError("cols/rows must be 1..8 and overlap must be 0..0.25")
    boxes = []
    for r in range(rows):
        for c in range(cols):
            w, h = 1 / cols, 1 / rows
            x0 = max(0., c * w - overlap * w / 2)
            y0 = max(0., r * h - overlap * h / 2)
            x1 = min(1., (c + 1) * w + overlap * w / 2)
            y1 = min(1., (r + 1) * h + overlap * h / 2)
            boxes.append((x0, y0, x1, y1))
    return boxes


def crop_pdf(pdf: Path, out: Path, cols: int, rows: int, overlap: float, long_edge: int) -> list[dict[str, Any]]:
    import pymupdf

    if long_edge < 1400 or long_edge > 7000:
        raise ValueError("--render-long-edge must be between 1400 and 7000")
    tiles: list[dict[str, Any]] = []
    pages = out / "pages"
    tiles_dir = out / "tiles"
    pages.mkdir(parents=True, exist_ok=True)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as doc:
        for page_idx, page in enumerate(doc, 1):
            scale = long_edge / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            from PIL import Image
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            fullpath = pages / f"page{page_idx:03d}.png"
            img.save(fullpath)
            for tile_idx, (x0, y0, x1, y1) in enumerate(grid_boxes(cols, rows, overlap), 1):
                x = tuple(map(int, (x0 * img.width, y0 * img.height, x1 * img.width, y1 * img.height)))
                clip = img.crop(x)
                # Large images are cropped *before* being resized, preserving small text.
                clip.thumbnail((2000, 2000))
                rel = f"tiles/page{page_idx:03d}_tile{tile_idx:02d}.png"
                clip.save(out / rel)
                tiles.append({"id": f"page{page_idx:03d}_tile{tile_idx:02d}", "page": page_idx,
                              "image": rel, "page_bbox": [round(n, 6) for n in (x0, y0, x1, y1)]})
            img.close()
    return tiles


def _run_tesseract(image: Path, lang: str) -> list[dict[str, Any]]:
    """Tesseract TSV is only an independent text witness, not a topology oracle."""
    proc = subprocess.run(
        ["tesseract", str(image), "stdout", "-l", lang, "--psm", "11", "tsv"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300] or "tesseract failed")
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for w in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t"):
        if w.get("level") != "5" or not w.get("text", "").strip():
            continue
        key = tuple(w.get(k, "") for k in ("page_num", "block_num", "par_num", "line_num"))
        groups[key].append(w)
    result = []
    for words in groups.values():
        words.sort(key=lambda w: int(w.get("left", 0)))
        x0 = min(int(w["left"]) for w in words)
        y0 = min(int(w["top"]) for w in words)
        x1 = max(int(w["left"]) + int(w["width"]) for w in words)
        y1 = max(int(w["top"]) + int(w["height"]) for w in words)
        # Keep raw spacing and both phrase and token-level text as evidence.
        result.append({"text": " ".join(w["text"] for w in words), "pixel_bbox": [x0, y0, x1, y1]})
    return result


def collect_ocr(pdf: Path, out: Path, tiles: list[dict[str, Any]], mode: str, lang: str) -> dict[str, Any]:
    import pymupdf
    ocr: dict[str, Any] = {"mode": mode, "language": lang, "pages": {}, "warnings": []}
    can_tess = bool(shutil.which("tesseract"))
    if mode == "tesseract" and not can_tess:
        raise RuntimeError("需要安装 tesseract-ocr 和 tesseract-ocr-chi-sim")
    with pymupdf.open(pdf) as doc:
        for page_idx, page in enumerate(doc, 1):
            matches: list[dict[str, Any]] = []
            for word in page.get_text("words"):
                x0, y0, x1, y1, txt = word[:5]
                matches.append({"text": str(txt), "bbox": [x0 / page.rect.width, y0 / page.rect.height,
                         x1 / page.rect.width, y1 / page.rect.height], "method": "pdf_text"})
            if (not matches and mode in {"auto", "tesseract"}) and can_tess:
                img = out / "pages" / f"page{page_idx:03d}.png"
                from PIL import Image
                with Image.open(img) as image:
                    width, height = image.size
                try:
                    for row in _run_tesseract(img, lang):
                        x0, y0, x1, y1 = row["pixel_bbox"]
                        matches.append({"text": row["text"], "bbox": [x0 / width, y0 / height,
                                x1 / width, y1 / height], "method": "tesseract"})
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as ex:
                    ocr["warnings"].append(f"page {page_idx}: OCR 失败: {ex}")
                    if mode == "tesseract":
                        raise
            if not matches:
                ocr["warnings"].append(f"page {page_idx}: 没有独立文字证据（缺少文字层或 OCR 不可用）")
            for j, item in enumerate(matches, 1):
                item["id"] = f"P{page_idx:03d}T{j:04d}"
                item["bbox"] = [round(v, 6) for v in item["bbox"]]
            ocr["pages"][str(page_idx)] = matches
    (out / "ocr.json").write_text(json.dumps(ocr, ensure_ascii=False, indent=2), encoding="utf-8")
    return ocr


class CudaQwen:
    """Lazy model loader. Importing fast_cable_route must not need GPU or Transformers."""

    def __init__(self, model_id: str, online: bool, max_new_tokens: int, seed: int) -> None:
        if not online:
            os.environ["HF_HUB_OFFLINE"] = "1"
        import torch
        from transformers import AutoProcessor, AutoModelForMultimodalLM, BitsAndBytesConfig
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用：请先运行你的 torch.cuda.is_available() 检查")
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_id, local_files_only=not online)
        quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                  bnb_4bit_compute_dtype=torch.bfloat16)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            model_id, quantization_config=quant, dtype=torch.bfloat16,
            device_map={"": 0}, low_cpu_mem_usage=True, local_files_only=not online,
        )
        self.model.eval()
        self.max_new_tokens = max_new_tokens
        self.seed = seed

    def infer(self, image: Path, index: int, prompt: str | None = None) -> dict[str, Any]:
        torch = self.torch
        msgs = [{"role": "user", "content": [
            {"type": "image", "path": str(image.resolve())}, {"type": "text", "text": prompt or PROMPT}]}]
        inputs = self.processor.apply_chat_template(
            msgs, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt", enable_thinking=False,
        ).to("cuda:0")
        torch.manual_seed(self.seed + index)
        torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        with torch.inference_mode():
            outputs = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens,
                                          do_sample=True, temperature=.7, top_p=.8, top_k=20)
        new = outputs[0][inputs["input_ids"].shape[-1]:]
        raw = self.processor.decode(new, skip_special_tokens=True)
        return {"raw": raw, "seconds": round(time.perf_counter() - start, 3),
                "generated_tokens": len(new),
                "max_tokens_reached": len(new) >= self.max_new_tokens,
                "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 1024**3, 3)}


def load_recorded_reply(folder: Path, tile_id: str) -> dict[str, Any]:
    for ext in (".txt", ".json"):
        path = folder / f"{tile_id}{ext}"
        if path.is_file():
            contents = path.read_text(encoding="utf-8")
            if ext == ".json":
                try:
                    obj = json.loads(contents)
                    if isinstance(obj, dict) and isinstance(obj.get("raw"), str):
                        return {"raw": obj["raw"], "source_file": str(path)}
                    return {"raw": contents, "source_file": str(path)}
                except ValueError:
                    pass
            return {"raw": contents, "source_file": str(path)}
    return {"raw": "", "missing_response": True}


def evidence_for(text: str | None, ocr_lines: list[dict[str, Any]]) -> list[str]:
    query = normalize_text(text)
    if not query:
        return []
    return [r["id"] for r in ocr_lines if query in normalize_text(r["text"]) or
            (len(query) >= 4 and normalize_text(r["text"]) in query and len(normalize_text(r["text"])) >= 4)]


def candidate_from_model(raw: dict[str, Any], page: int, tile_id: str, ocr_lines: list[dict[str, Any]], number: int) -> dict[str, Any]:
    start = text_or_none(raw.get("起点"))
    end = text_or_none(raw.get("终点"))
    typ = text_or_none(raw.get("类型"))
    cores, c_err = parse_cores(raw.get("芯数"))
    length, l_err = parse_length(raw.get("长度km"))
    turns = text_or_none(raw.get("回路数"))
    construction = text_or_none(raw.get("建设状态"))
    notes: list[str] = []
    blockers: list[str] = []
    if not start or not end:
        blockers.append("站点端点缺失")
    elif normalize_text(start) == normalize_text(end):
        blockers.append("自环：起点与终点相同")
    if c_err:
        notes.append(c_err)
    if l_err:
        notes.append(l_err)
    if typ and not re.fullmatch(r"(?:OPGW|ADSS|OPPC|GYTA\w*|GJ\w*|其他)", typ, re.I):
        notes.append("光缆类型未识别为常见类型，需核对")
    if construction and construction not in {"本工程新建", "已有光缆", "已有或其他工程待建", "其他工程待建", "待建"}:
        notes.append("建设状态未能对应图例")
    if construction == "已有光缆":
        notes.append("细线图例可能包含其他工程待建，不能据线型仅认定已建")
    ev = {"起点": evidence_for(start, ocr_lines), "终点": evidence_for(end, ocr_lines),
          "芯数": evidence_for(str(cores) if cores else None, ocr_lines),
          "长度": evidence_for(str(length) if length else None, ocr_lines)}
    if not ev["起点"]:
        notes.append("OCR未独立核实起点名称")
    if not ev["终点"]:
        notes.append("OCR未独立核实终点名称")
    if not ev["芯数"] or not ev["长度"]:
        notes.append("芯数或长度未获得独立文字证据")
    notes.append("连线几何与光缆参数归属需分别核对")
    return {
        "id": f"C{number:04d}", "page": page, "tile_id": tile_id,
        "start": start, "end": end, "cable_type": typ, "cores": cores,
        "length_km": length, "circuits": turns, "construction": construction,
        "status": "拒绝" if blockers else "待核对", "blockers": blockers, "notes": list(dict.fromkeys(notes)),
        "ocr_ids": ev, "source_raw": raw,
        "evidence_check": "端点冲突" if blockers else ("仅有文字线索，拓扑未核实" if any(ev.values()) else "缺少独立文字证据"),
    }


def deduplicate(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse only identical model candidates from overlapping tiles. Preserve alternatives."""
    result: list[dict[str, Any]] = []
    keys: dict[tuple, dict[str, Any]] = {}
    for c in candidates:
        endpoints = tuple(sorted((normalize_text(c["start"]), normalize_text(c["end"]))))
        key = (c["page"], endpoints, normalize_text(c["cable_type"]), c["cores"],
               c["length_km"], normalize_text(c["circuits"]), normalize_text(c["construction"]))
        if key in keys:
            previous = keys[key]
            previous.setdefault("other_tiles", []).append(c["tile_id"])
            previous["notes"] = list(dict.fromkeys(previous["notes"] + c["notes"]))
            for field, ids in c["ocr_ids"].items():
                previous["ocr_ids"][field] = list(dict.fromkeys(previous["ocr_ids"][field] + ids))
            continue
        keys[key] = c
        result.append(c)
    return result


def crosscheck_parallel(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for c in rows:
        endpoints = tuple(sorted((normalize_text(c["start"]), normalize_text(c["end"]))))
        groups[(c["page"], endpoints)].append(c)
    for peers in groups.values():
        if len(peers) > 1:
            for row in peers:
                row["notes"].append("同一端点存在多条不同参数候选：可能是并行光缆或模型冲突，请逐条核对")


def tabular(rows: list[dict[str, Any]]) -> list[list[Any]]:
    output = []
    for c in rows:
        output.append([c["id"], c["page"], c["start"], c["end"], c["cable_type"],
                       c["cores"], c["length_km"], c["circuits"], c["construction"],
                       c["status"], c["evidence_check"], "；".join(c["blockers"] + c["notes"]),
                       "; ".join(f"{k}: {','.join(v)}" for k, v in c["ocr_ids"].items() if v),
                       f"tiles/{c['tile_id']}.png", "",
                       c.get("geometry_check", {}).get("state", "未执行"),
                       ",".join(c.get("geometry_check", {}).get("nodes", [])),
                       c.get("geometry_check", {}).get("edge_id") or ""])
    return output


def _xlsx_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    # Excel formula injection protection for untrusted model/OCR text.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_xlsx(out: Path, rows: list[dict[str, Any]], ocr: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Use the existing repo's openpyxl dependency in the Ubuntu runtime."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    info = wb.active
    info.title = "说明"
    info.append(["光缆路由图 Fast 候选（未确认工程事实）", ""])
    info.append(["PDF", manifest["pdf_name"]])
    info.append(["SHA256", manifest["pdf_sha256"]])
    info.append(["模型", manifest["model"]])
    info.append(["版本", manifest["version"]])
    info.append(["候选条数", len(rows)])
    info.append(["待核对", sum(r["status"] == "待核对" for r in rows)])
    info.append(["自动拒绝", sum(r["status"] == "拒绝" for r in rows)])
    info.append(["说明", "所有未被人工签认的连接均为待核对；OCR字面命中不证明连线归属。"])
    info.append(["复核方式", "在光缆候选页编辑字段及核对状态；请勿覆盖原始OCR和调用记录。"])
    info.append(["OCR警告", "；".join(ocr["warnings"])[:1000]])
    info.append(["几何核验", "仅检测PDF中清晰圆形站点的直接矢量线段；未检出不等于不存在；参数归属仍需人工核对。"] )
    info.append(["站点映射", "在geometry/station_anchors.csv的确认站名列填写站点，下一次使用--station-map重新处理；仅人工填写的名称用于几何端点匹配。"] )
    info.column_dimensions["A"].width = 25
    info.column_dimensions["B"].width = 88
    info["A1"].font = Font(bold=True, size=16, color="17365D")
    for row in info:
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    ws = wb.create_sheet("光缆候选")
    ws.append(HEADERS)
    for values in tabular(rows):
        ws.append([_xlsx_safe(v) for v in values])
    widths = [13, 9, 22, 22, 15, 9, 14, 11, 18, 14, 28, 65, 48, 35, 36, 42, 24, 16]
    for j, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:R{max(2,ws.max_row)}"
    ws.row_dimensions[1].height = 30
    ws.sheet_view.zoomScale = 85
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for i in range(2, ws.max_row + 1):
        ws.row_dimensions[i].height = 42
        for cell in ws[i]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if i % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F2F6FB")
        ws.cell(i, 10).fill = PatternFill("solid", fgColor="FCE8B2" if ws.cell(i, 10).value == "待核对" else "F9D6D5")
        link_cell = ws.cell(i, 14)
        link_cell.hyperlink = link_cell.value
        link_cell.style = "Hyperlink"
    status_validation = DataValidation(type="list", formula1='"待核对,已确认,已纠正,拒绝"', allow_blank=False)
    status_validation.promptTitle = "人工核对"
    status_validation.prompt = "请审查源图和标注后再确认，不要只相信模型"
    ws.add_data_validation(status_validation)
    status_validation.add("J2:J5000")
    ocr_sheet = wb.create_sheet("OCR文字证据")
    ocr_sheet.append(["证据ID", "页码", "原文", "提取方式", "归一化坐标 (x0,y0,x1,y1)"])
    for page, lines in ocr["pages"].items():
        for line in lines:
            ocr_sheet.append([line["id"], int(page), _xlsx_safe(line["text"]), line["method"], json.dumps(line["bbox"])])
    ocr_sheet.freeze_panes = "A2"
    ocr_sheet.auto_filter.ref = f"A1:E{max(2,ocr_sheet.max_row)}"
    for col, width in zip("ABCDE", [20, 9, 80, 16, 48]):
        ocr_sheet.column_dimensions[col].width = width
    for cell in ocr_sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="2B6777")
    if ocr_sheet.max_row >= 2:
        ocr_sheet.row_dimensions[2].height = 24
    out_file = out / "structured.xlsx"
    wb.save(out_file)


def export_html(out: Path, rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    body = ["<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
            "<title>光缆路由图候选复核</title>",
            "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;max-width:1150px;margin:25px auto;color:#203040;padding:0 12px}"
            "table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}"
            "th{background:#e9f0f9}img{max-width:100%;border:1px solid #aaa}summary{cursor:pointer}"
            ".warn{color:#a35400}.bad{color:#b42318}</style></head><body>",
            "<h1>光缆路由图 Fast 候选复核</h1>",
            "<p class='warn'>仅有直接矢量线段的保守几何候选；不核实光缆属性/标签归属，缺线不证明断开，所有记录仍待人工确认。</p>",
            f"<p>文件：{html.escape(manifest['pdf_name'])}；候选 {len(rows)} 条。"
            "<a href='structured.xlsx'>编辑候选 Excel</a> · <a href='candidates.json'>原始候选 JSON</a> · <a href='ocr.json'>OCR</a> · "
            "<a href='geometry/station_anchors.csv'>圆形站点标注CSV</a> · <a href='geometry/geometry.json'>几何数据</a></p>",
            "<table><thead><tr><th>候选</th><th>端点</th><th>光缆</th><th>几何核验</th><th>状态 / 提示</th><th>证据图</th></tr></thead><tbody>"]
    for c in rows:
        img = f"tiles/{c['tile_id']}.png"
        attributes = f"{c['cable_type'] or '未知'} / {c['cores'] if c['cores'] is not None else '?'}芯 / {c['length_km'] if c['length_km'] is not None else '?'}km"
        reasons = "；".join(c["blockers"] + c["notes"])
        body.append("<tr><td>" + html.escape(c["id"]) + "</td><td>" +
                    html.escape(f"{c['start'] or '?'} → {c['end'] or '?'}") +
                    "</td><td>" + html.escape(attributes) + "</td><td>" + html.escape(c.get("geometry_check", {}).get("state", "未执行")) + "</td><td class='warn'>" +
                    html.escape(c["status"] + "；" + reasons) + "</td><td><a href='" + html.escape(img, quote=True) +
                    "' target='_blank'>打开局部图</a></td></tr>")
    body += ["</tbody></table><h2>圆形站点与直连矢量线段</h2>",
             "<p>蓝橙色圈为检测节点，绿色线为直接矢量线段；不是人工签认的光缆路由。",
             "<a href='geometry/page001_overlay.png' target='_blank'>打开第一页几何叠加图</a>；多页请在geometry目录查看。</p>",
             "<h2>核对说明</h2><p>点击局部图后核对原始端点和标注；在 Excel 中人工更正并设置核对状态。",
             "不要仅凭 OCR 字面匹配认定真实拓扑；Excel 修改不会自动覆盖 candidates.json。</p></body></html>"]
    (out / "review.html").write_text("\n".join(body), encoding="utf-8")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="电力通信光缆路由图 PDF")
    parser.add_argument("--output", type=Path, required=True, help="全新输出目录（禁止覆盖已有目录）")
    parser.add_argument("--model-id", default=MODEL_DEFAULT)
    parser.add_argument("--responses-dir", type=Path, help="从事先保存的 page001_tile01.txt/.json 读取模型回答，免 GPU")
    parser.add_argument("--online", action="store_true", help="允许 Hugging Face 下载；默认本地缓存离线")
    parser.add_argument("--ocr", choices=["auto", "tesseract", "none"], default="auto")
    parser.add_argument("--ocr-lang", default="chi_sim+eng")
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument("--rows", type=int, default=2)
    parser.add_argument("--overlap", type=float, default=.12)
    parser.add_argument("--render-long-edge", type=int, default=3600)
    parser.add_argument("--max-new-tokens", type=int, default=640)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--geometry", choices=["auto", "off"], default="auto",
                        help="圆形站点与直接矢量线段核验，默认启用")
    parser.add_argument("--station-map", type=Path,
                        help="填写过的geometry/station_anchors.csv，映射人工确认站名到节点")
    parser.add_argument("--geometry-only", action="store_true",
                        help="只生成几何叠加图、站点节点映射与空的复核工作簿，不加载Qwen")
    parser.add_argument("--node-names", choices=["off", "ocr", "qwen"], default="off",
                        help="站名与圆形节点候选匹配；qwen 使用本地模型，ocr 仅 OCR")
    parser.add_argument("--name-max-nodes", type=int, default=40,
                        help="最多识别的圆形节点数量")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    pdf = args.pdf.expanduser().resolve()
    out = args.output.expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError(f"输入必须是存在的PDF文件：{pdf}")
    if out.exists():
        raise FileExistsError(f"输出目录已存在，禁止覆盖现有审查结果：{out}")
    if args.max_new_tokens < 64 or args.max_new_tokens > 4096:
        raise ValueError("--max-new-tokens 要在 64..4096 范围")
    # Ensure simple parameter errors cannot create partial output directories.
    grid_boxes(args.cols, args.rows, args.overlap)
    if not 1400 <= args.render_long_edge <= 7000:
        raise ValueError("--render-long-edge 必须在 1400..7000")
    if args.responses_dir and not args.responses_dir.is_dir():
        raise ValueError(f"记录的回答目录不存在：{args.responses_dir}")
    out.mkdir(parents=True)
    tiles = crop_pdf(pdf, out, args.cols, args.rows, args.overlap, args.render_long_edge)
    ocr = collect_ocr(pdf, out, tiles, args.ocr, args.ocr_lang)
    geometry = None
    if args.geometry == "auto":
        from cable_route_geometry import build_geometry
        geometry = build_geometry(pdf, out, ocr, args.station_map)
        print("几何：", sum(len(p["nodes"]) for p in geometry["pages"]),
              "个圆形站点候选；", sum(len(p["edges"]) for p in geometry["pages"]),
              "条直接矢量线段候选", flush=True)
    elif args.station_map:
        raise ValueError("--station-map 需要启用 --geometry auto")
    manifest = {"version": VERSION, "pdf_name": pdf.name, "pdf_sha256": sha256(pdf),
                "geometry_enabled": bool(geometry),
                "model": args.model_id if not args.responses_dir else "recorded-responses (no inference)",
                "generation_mode": "geometry-only" if args.geometry_only else ("replay" if args.responses_dir else "cuda-4bit"),
                "tile_count": len(tiles), "ocr_mode": args.ocr,
                "note": "所有非拒绝候选均待人工核对；OCR文字存在不构成连接关系证据"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "tiles.json").write_text(json.dumps(tiles, ensure_ascii=False, indent=2), encoding="utf-8")
    calls = out / "calls"
    calls.mkdir()
    # GPU loaded once only after rendering / OCR / geometry completes.
    engine = None if (args.responses_dir or args.geometry_only) else CudaQwen(args.model_id, args.online, args.max_new_tokens, args.seed)
    original: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for idx, t in enumerate([] if args.geometry_only else tiles):
        print(f"[{idx + 1}/{len(tiles)}] {t['id']}", flush=True)
        try:
            result = load_recorded_reply(args.responses_dir, t["id"]) if args.responses_dir else engine.infer(out / t["image"], idx)
        except Exception as ex:
            result = {"raw": "", "error": f"{type(ex).__name__}: {ex}"}
            if engine is not None and "out of memory" in str(ex).lower():
                engine.torch.cuda.empty_cache()
        result.update({"tile_id": t["id"], "page": t["page"], "image": t["image"]})
        parsed, err = parse_reply(result.get("raw", ""))
        if result.get("missing_response"):
            err = "该切片没有保存模型回复"
        if result.get("error"):
            err = result["error"]
        result["parse_error"] = err
        (calls / f"{t['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if err:
            unresolved.append({"tile_id": t["id"], "page": t["page"], "reason": err})
            continue
        assert parsed is not None
        original.append({"tile_id": t["id"], "page": t["page"], "reply": parsed})
        if result.get("max_tokens_reached"):
            unresolved.append({"tile_id": t["id"], "page": t["page"], "reason": "触及生成Token上限，可能遗漏连接"})
        for msg in parsed.get("待核对项", []):
            if isinstance(msg, str):
                unresolved.append({"tile_id": t["id"], "page": t["page"], "reason": msg})
        for raw_record in parsed.get("光缆连接", [])[:16]:
            if not isinstance(raw_record, dict):
                unresolved.append({"tile_id": t["id"], "page": t["page"], "reason": "非对象记录已跳过"})
                continue
            rows.append(candidate_from_model(raw_record, t["page"], t["id"],
                                             ocr["pages"].get(str(t["page"]), []), len(rows) + 1))
    rows = deduplicate(rows)
    crosscheck_parallel(rows)
    if geometry is not None:
        from cable_route_geometry import check_candidate
        for record in rows:
            verdict = check_candidate(record, geometry)
            record["geometry_check"] = verdict
            if verdict["state"].startswith("未检出"):
                record["notes"].append("几何未找到直接路径；折线或遮挡可能漏检，请优先复核")
            # Never treat a detected line as proof that its cable properties match.
    for i, row in enumerate(rows, 1):
        row["id"] = f"C{i:04d}"
    report = {"manifest": manifest, "records": rows, "unresolved": unresolved, "raw_model_replies": original,
              "geometry": {"enabled": bool(geometry), "station_map": bool(args.station_map)},
              "warning": "OCR 仅核对字面，不核验站点之间是否真正连通，全部记录需要人工核对"}
    (out / "candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as fd:
        writer = csv.writer(fd)
        writer.writerow(HEADERS)
        writer.writerows(tabular(rows))
    export_xlsx(out, rows, ocr, manifest)
    export_html(out, rows, manifest)
    if args.node_names != "off":
        if args.geometry == "off":
            raise ValueError("--node-names 需要启用 --geometry auto")
        from station_node_linker import run as link_station_names
        selected_mode = args.node_names if engine is not None else "ocr"
        if args.node_names == "qwen" and engine is None:
            print("无模型推理模式：节点站名识别回退到 OCR", flush=True)
        node_result = link_station_names(pdf, out, mode=selected_mode,
            max_nodes=args.name_max_nodes, existing_backend=engine,
            lang=args.ocr_lang, online=args.online, model_id=args.model_id)
        print("新节点复核页面:", node_result["result_html"], flush=True)
    print(f"\n候选 {len(rows)} 条，未解决项 {len(unresolved)} 条")
    print(f"Excel: {out / 'structured.xlsx'}")
    print(f"核对页: {out / 'review.html'}")
    return report


def main() -> int:
    args = make_parser().parse_args()
    try:
        run(args)
    except Exception as ex:
        print(f"错误：{type(ex).__name__}: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
