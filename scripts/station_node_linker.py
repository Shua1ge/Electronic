#!/usr/bin/env python3
"""Link drawn station-circle IDs to *candidate* station names (never confirmed facts).

Works as a postprocessor for the Electronic Fast v0.2 result directory:
    python scripts/station_node_linker.py ~/文档/03.pdf ~/fast-cable-03-v02 --mode ocr
    python scripts/station_node_linker.py ~/文档/03.pdf ~/fast-cable-03-v02 --mode qwen

Preserves original structured.xlsx, candidates.json and review.html. Generates:
  geometry/station_name_crops/     per-node image and targeted colored-ring version
  geometry/station_name_candidates.json
  geometry/station_name_candidates.csv
  geometry/candidate_name_checks.json
  candidates-with-nodes.json
  structured-with-nodes.xlsx
  review-with-nodes.html

Only exact, uniquely located name hypotheses produce provisional node-to-name links.
One-character near matches are *suggestions*, never silent corrections.
An edge in the PDF does not prove cable properties belong to that edge.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROMPT = (
    '这张图是电力通信光缆路由图的一个局部截图。红色圆圈标记了一个目标站点节点。'
    '只读取紧挨着这个红圈的站点/变电站名称，不要读取其他圆圈的名称，也不要把OPGW、芯数、km等线路标注当作站名。'
    '必须逐字按图片读取，不要根据地名常识纠正生僻字。如果某字模糊，可以给出不同读法。'
    '只输出一个JSON对象：{"站名":"原图站名或null","其他可能读法":[],"看不清":false}。'
    '如果无法确认主读法，站名填null，不要强行猜。不要Markdown。'
)


def norm(text: Any) -> str:
    return re.sub(r'[\s（）()·—–\-_,，.。:：/]+', '', str(text or '')).casefold()


def clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().strip('"\'「」[]{}')
    v = re.sub(r'\s+', '', v)
    if not v or v.lower() in ('null', 'none', 'unknown', '不清楚', '未知', '看不清'):
        return None
    if not (2 <= len(v) <= 22):
        return None
    # Cable specification and vector labels are not names, even if their text is legible.
    if re.search(r'OPGW|ADSS|OPPC|\b\d+(?:\.\d+)?\s*km\b|\d+芯|光缆|回路', v, re.I):
        return None
    if not re.search('[\u4e00-\u9fff]', v):
        return None
    # Discard common OCR fragments such as "i)兴" and "Py钞"; allow actual
    # voltage-prefixed labels (500kV十堰变) and alphanumeric station IDs (A站).
    if v.count('（') != v.count('）') or v.count('(') != v.count(')'):
        return None
    if re.search(r'[A-Za-z]',v) and not re.fullmatch(
            r'(?:\d{2,4}[kK][vV])?[\u4e00-\u9fff0-9（）()]+|[A-Z0-9]{1,8}[\u4e00-\u9fff]{1,8}', v):
        return None
    return v


def clean_reply(raw: str) -> tuple[str | None, list[str], str | None]:
    s = raw.strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, count=1, flags=re.I)
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3].strip()
    try:
        data = json.loads(s)
    except (ValueError, TypeError) as e:
        return None, [], f'JSON未解析：{str(e)[:95]}'
    if not isinstance(data, dict):
        return None, [], '模型未返回JSON对象'
    main = clean_name(data.get('站名', data.get('name')))
    alts = data.get('其他可能读法', data.get('alternatives', []))
    if not isinstance(alts, list):
        alts = []
    names = []
    for item in alts[:4]:
        v = clean_name(item)
        if v and norm(v) != norm(main) and v not in names:
            names.append(v)
    return main, names, None


def geometry_pages(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    pages = geometry.get('pages', [])
    if isinstance(pages, dict):
        return [pages[k] for k in sorted(pages, key=lambda x: int(x))]
    if isinstance(pages, list):
        return pages
    raise ValueError('geometry.json 的 pages 必须是列表或对象')


def node_location(node: dict[str, Any]) -> tuple[float, float, float]:
    if 'x_pt' in node and 'y_pt' in node:
        return float(node['x_pt']), float(node['y_pt']), float(node.get('radius_pt', 5))
    if isinstance(node.get('center'), (list, tuple)) and len(node['center']) == 2:
        return float(node['center'][0]), float(node['center'][1]), float(node.get('radius', 5))
    raise ValueError(f"节点 {node.get('id')} 缺少有效坐标")


def page_dimensions(page: dict[str, Any]) -> tuple[float, float]:
    return float(page.get('width_pt', page.get('width'))), float(page.get('height_pt', page.get('height')))


def render_crop(page, node: dict[str, Any], folder: Path, index: int, zoom: float = 4) -> tuple[str, str, dict[str, Any]]:
    import pymupdf
    from PIL import Image, ImageDraw
    x, y, radius = node_location(node)
    # 85 pt covers typical adjacent station captions, with ~40 pt vertical clearance.
    clip = pymupdf.Rect(x - 88, y - 42, x + 88, y + 48) & page.rect
    if clip.is_empty:
        raise ValueError('站点裁剪区域为空')
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    im = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
    name = f"page{index:03d}_{node['id']}"
    raw_path = folder / f'{name}_raw.png'
    mark_path = folder / f'{name}_marked.png'
    im.save(raw_path)
    d = ImageDraw.Draw(im)
    xx, yy = (x - clip.x0) * zoom, (y - clip.y0) * zoom
    rr = max(9., radius * zoom + 5)
    d.ellipse((xx - rr, yy - rr, xx + rr, yy + rr), outline=(230, 35, 30), width=3)
    im.save(mark_path)
    return raw_path.name, mark_path.name, {'pdf_bbox': [round(v, 3) for v in tuple(clip)], 'center_pdf': [x, y]}


def tesseract_read(image: Path, lang: str = 'chi_sim+eng') -> list[dict[str, Any]]:
    if not shutil.which('tesseract'):
        return []
    try:
        p = subprocess.run(['tesseract', str(image), 'stdout', '-l', lang, '--psm', '11', 'tsv'],
                           capture_output=True, text=True, timeout=45)
        if p.returncode != 0:
            return []
        rows = csv.DictReader(io.StringIO(p.stdout), delimiter='\t')
        groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            if row.get('level') == '5' and (row.get('text') or '').strip():
                key = tuple(row.get(k, '') for k in ('block_num', 'par_num', 'line_num'))
                groups[key].append(row)
        results = []
        for words in groups.values():
            words.sort(key=lambda w: int(w['left']))
            value = ''.join((w['text'] or '').strip() for w in words)
            name = clean_name(value)
            if not name:
                continue
            x0 = min(int(w['left']) for w in words)
            x1 = max(int(w['left']) + int(w['width']) for w in words)
            y0 = min(int(w['top']) for w in words)
            y1 = max(int(w['top']) + int(w['height']) for w in words)
            results.append({'name': name, 'pixel_bbox': [x0, y0, x1, y1], 'source': 'crop_tesseract'})
        return results
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def ocr_near_node(node: dict[str, Any], page_ocr: list[dict[str, Any]], w: float, h: float) -> list[dict[str, Any]]:
    x, y, _ = node_location(node)
    out = []
    for line in page_ocr:
        box = line.get('bbox')
        if not box or len(box) != 4:
            continue
        name = clean_name(line.get('text'))
        if not name:
            continue
        # Use distance from *text rectangle*, not its center: long 500kV names
        # are often right- or left-aligned to a circle.
        x0, y0, x1, y1 = float(box[0])*w, float(box[1])*h, float(box[2])*w, float(box[3])*h
        dx = max(x0-x, x-x1, 0)
        dy = max(y0-y, y-y1, 0)
        if dx <= 36 and dy <= 27 and math.hypot(dx, dy) <= 38:
            out.append({'name': name, 'source': 'page_ocr', 'ocr_id': line.get('id'),
                        'distance_pt': round(math.hypot(dx,dy),2)})
    return sorted(out, key=lambda a: a['distance_pt'])[:3]


def dedup_ocr_names(names: list[dict[str, Any]], center: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for n in names:
        key = norm(n.get('name'))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(n)
    return result[:4]


def node_candidate(node: dict[str, Any], ocr: list[dict[str, Any]], vlm: dict[str, Any],
                   confirmed: str | None = None) -> dict[str, Any]:
    """Propose a name only, never treat model or OCR as an operator approval."""
    main = vlm.get('name')
    ocr_names = [v['name'] for v in ocr]
    options: list[dict[str, str]] = []
    if confirmed:
        options.append({'name': confirmed, 'source': '人工'} )
    if main:
        options.append({'name': main, 'source': 'Qwen'})
    for alt in vlm.get('alternatives', []):
        if alt:
            options.append({'name': alt, 'source': 'Qwen其他读法'})
    for name in ocr_names:
        options.append({'name': name, 'source': 'OCR'})
    options = [o for i,o in enumerate(options) if norm(o['name']) not in {norm(a['name']) for a in options[:i]}]
    primary = None
    status = ''
    if confirmed:
        primary, status = confirmed, '人工已填写（需按人工流程签认）'
    elif main:
        if vlm.get("alternatives"):
            status = "Qwen提供多种读法（不自动确定）"
            return {"node_id": node["id"], "primary_name": None, "state": status,
                    "name_options": options, "ocr_readings": ocr, "vlm_reading": vlm,
                    "confirmed_name": confirmed}
        agreeing = [n for n in ocr_names if norm(n) == norm(main)]
        disagreeing = [n for n in ocr_names if norm(n) != norm(main)]
        if agreeing:
            primary, status = main, 'OCR与Qwen一致（未签认）'
        elif disagreeing:
            status = 'OCR与Qwen不同读法（不自动确定）'
        else:
            primary, status = main, '仅Qwen候选（未签认）'
    elif len({norm(n) for n in ocr_names}) == 1 and ocr_names:
        primary, status = ocr_names[0], '仅OCR候选（未签认）'
    elif ocr_names:
        status = 'OCR多种读法（不自动确定）'
    else:
        status = '未读出站名'
    return {'node_id': node['id'], 'primary_name': primary, 'state': status,
            'name_options': options, 'ocr_readings': ocr, 'vlm_reading': vlm,
            'confirmed_name': confirmed}


def _edit_distance_le_one(a: str, b: str) -> bool:
    # Exact vs one changed/inserted/deleted character, no automatic correction.
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x,y in zip(a,b)) == 1
    if len(a) > len(b):
        a,b = b,a
    i=j=errors=0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
        else:
            errors += 1; j += 1
            if errors>1: return False
    return True


def _edge_ends(edge: dict[str, Any]) -> tuple[str | None, str | None]:
    return edge.get('a_node', edge.get('a')), edge.get('b_node', edge.get('b'))


def link_record(record: dict[str, Any], nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """Conservative exact unique match, never accepts fuzzy names as true ends."""
    all_primary: dict[str, set[str]] = defaultdict(set)
    all_options: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        if node.get('primary_name'):
            all_primary[norm(node['primary_name'])].add(node['node_id'])
        for option in node.get('name_options', []):
            all_options[norm(option['name'])].add(node['node_id'])
    matches, suggestions = [], {}
    for role, value in [('起点',record.get('start')), ('终点',record.get('end'))]:
        key = norm(value)
        owners = all_primary.get(key,set())
        if len(owners) == 1:
            matches.append(next(iter(owners)))
        else:
            matches.append(None)
            hints = []
            if key:
                for node in nodes:
                    for opt in node.get('name_options', []):
                        name = opt['name']
                        same = key == norm(name)
                        if same and node.get('primary_name') is None:
                            hints.append({'node_id':node['node_id'],'candidate_name':name,
                                          'source': opt['source'], 'reason':'该节点读法有冲突，暂不自动绑定'})
                        elif not same and _edit_distance_le_one(key,norm(name)):
                            hints.append({'node_id':node['node_id'],'candidate_name':name,
                                          'source': opt['source'], 'reason':'一字近形/编辑差异，仅提示'})
            # Unambiguous optional alternate readings are not silently promoted.
            suggestions[role] = hints[:5]
    if matches[0] and matches[1] and matches[0] == matches[1]:
        state = '自动读法指向同一个节点（端点冲突，需核对）'
        edge_id = None
    elif all(matches):
        edge = next((e for e in edges if set(_edge_ends(e)) == set(matches)), None)
        edge_id = edge.get('id') if edge else None
        if edge_id:
            state = '自动站名候选已匹配，检测到直接线段（站名/参数仍需核对）'
        else:
            state = '自动站名候选已匹配，但未检出直接线段（可能漏检）'
    elif any(suggestions.values()):
        edge_id = None
        state = '站名存在相近读法，未自动绑定全部端点'
    else:
        edge_id = None
        state = '站名未唯一匹配圆形节点（保持待核对）'
    return {'state':state,'node_ids':matches,'edge_id':edge_id,'suggestions':suggestions,
            'approval':'未人工确认', 'note':'文字一致不代表真实站名，矢量连线存在不代表芯数或长度归属正确'}


class QwenNameReader:
    """Reuse an existing Fast CudaQwen instance or load the same cached 2B once."""
    def __init__(self, existing: Any = None, model_id: str = 'Qwen/Qwen3.5-2B', online: bool = False):
        if existing is None:
            if not online:
                os.environ['HF_HUB_OFFLINE'] = '1'
            import torch
            from transformers import AutoProcessor, AutoModelForMultimodalLM, BitsAndBytesConfig
            self.torch = torch
            self.processor = AutoProcessor.from_pretrained(model_id, local_files_only=not online)
            self.model = AutoModelForMultimodalLM.from_pretrained(
                model_id,
                local_files_only=not online,
                quantization_config=BitsAndBytesConfig(load_in_4bit=True,
                    bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16),
                dtype=torch.bfloat16, device_map={'': 0}, low_cpu_mem_usage=True)
            self.model.eval()
        else:
            self.torch = existing.torch
            self.processor = existing.processor
            self.model = existing.model

    def read(self, image: Path, index: int) -> dict[str, Any]:
        torch = self.torch
        messages = [{'role':'user', 'content':[
            {'type':'image','path':str(image.resolve())},
            {'type':'text','text':PROMPT},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt', enable_thinking=False).to('cuda:0')
        torch.manual_seed(9917 + index)
        try:
            with torch.inference_mode():
                generated = self.model.generate(**inputs, max_new_tokens=95,
                     do_sample=True, temperature=.7, top_p=.8, top_k=20)
            new = generated[0][inputs['input_ids'].shape[-1]:]
            raw = self.processor.decode(new, skip_special_tokens=True)
            name, alternatives, error = clean_reply(raw)
            return {'name':name,'alternatives':alternatives,'raw':raw,'error':error,
                    'reached_limit':len(new)>=95}
        finally:
            # Avoid accumulating image tensors across 20+ node inspections.
            del inputs


def _html_escape(s: Any) -> str:
    return html.escape(str(s if s is not None else ''))


def _xlsx_safe(s: Any) -> Any:
    return "'"+s if isinstance(s,str) and s[:1] in '=+-@' else s


def export_reports(out: Path, pages: list[dict[str, Any]], nodes: list[dict[str, Any]],
                   checked: list[dict[str, Any]]) -> None:
    store = out/'geometry'
    (store/'station_name_candidates.json').write_text(
        json.dumps({'pages':pages,'nodes':nodes,'warning':'站名仅是候选；不凭一字近形差异改字；人工核验前不签认'}, ensure_ascii=False,indent=2),encoding='utf-8')
    with (store/'station_name_candidates.csv').open('w',encoding='utf-8-sig',newline='') as fd:
        writer = csv.writer(fd)
        writer.writerow(['页码','节点ID','站名候选','状态','OCR读法','Qwen读法','其他可能读法','高清裁图','人工确认站名'])
        for n in nodes:
            writer.writerow([n['page'],n['node_id'], _xlsx_safe(n.get('primary_name') or ''),n['state'],
                             _xlsx_safe('、'.join(x['name'] for x in n['ocr_readings'])),
                             _xlsx_safe(n.get('vlm_reading',{}).get('name') or ''),
                             _xlsx_safe('、'.join(n.get('vlm_reading',{}).get('alternatives',[]))),
                             n['crop_marked'],n.get('confirmed_name') or ''])
    (store/'candidate_name_checks.json').write_text(json.dumps(checked,ensure_ascii=False,indent=2),encoding='utf-8')
    src = out/'candidates.json'
    if src.exists():
        data = json.loads(src.read_text(encoding='utf-8'))
        lookup = {r['candidate_id']:r for r in checked}
        for item in data.get('records',[]):
            item['auto_station_link'] = lookup.get(item.get('id'))
        (out/'candidates-with-nodes.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        workbook = load_workbook(out/'structured.xlsx')
        ws = workbook.create_sheet('站点名称候选')
        ws.append(['页码','节点ID','站名候选','核对状态','OCR候选','Qwen候选','原图裁剪'])
        for n in nodes:
            ws.append([n['page'],n['node_id'],_xlsx_safe(n.get('primary_name') or ''),n['state'],
                       _xlsx_safe('、'.join(o['name'] for o in n['ocr_readings'])),
                       _xlsx_safe(n['vlm_reading'].get('name') or ''),n['crop_marked']])
        for cell in ws[1]:
            cell.font = Font(bold=True,color='FFFFFF')
            cell.fill = PatternFill('solid',fgColor='344969')
        for letter,width in zip('ABCDEFG',[9,13,28,42,32,30,55]):
            ws.column_dimensions[letter].width = width
        ws.freeze_panes='A2'; ws.auto_filter.ref=f'A1:G{max(2,ws.max_row)}'
        vs = workbook.create_sheet('自动节点匹配')
        vs.append(['候选ID','起点','终点','自动匹配结果','起点节点','终点节点','几何线段','近形/歧义提示'])
        for r in checked:
            link = r['link']
            vs.append([r['candidate_id'],_xlsx_safe(r.get('start')),_xlsx_safe(r.get('end')),
                       link['state'], *(link['node_ids'] or [None,None]),link['edge_id'],
                       _xlsx_safe(json.dumps(link['suggestions'],ensure_ascii=False))])
        for cell in vs[1]:
            cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='344969')
        for letter,width in zip('ABCDEFGH',[12,23,23,65,14,14,14,70]):
            vs.column_dimensions[letter].width=width
        vs.freeze_panes='A2';vs.auto_filter.ref=f'A1:H{max(2,vs.max_row)}'
        workbook.save(out/'structured-with-nodes.xlsx')
    except ImportError:
        pass
    base = (out/'review.html').read_text(encoding='utf-8') if (out/'review.html').exists() else '<html><body><h1>站名绑定复核</h1></body></html>'
    content = ["<section id='station-name-link' style='margin-top:28px'>",
        '<h2>自动站名—圆形节点候选（未签认）</h2>',
        '<p>红圈为本次识别目标。OCR/Qwen 不同读法全部保留；未检出几何线段不等于断开；不自动改字。</p>',
        '<table><thead><tr><th>页 / 节点</th><th>站名候选</th><th>读法来源</th><th>核对状态</th><th>目标节点原图</th></tr></thead><tbody>']
    for n in nodes:
        vlm=n.get('vlm_reading',{})
        ocr_names=', '.join(v['name'] for v in n['ocr_readings']) or '(无)'
        qwen=vlm.get('name') or '(无)'
        alts=', '.join(vlm.get('alternatives',[])) or '(无)'
        content.append('<tr><td>'+_html_escape(f"p{n['page']} {n['node_id']}")+'</td><td><b>'+
            _html_escape(n.get('primary_name') or '待核对')+'</b></td><td>'+_html_escape('OCR: '+ocr_names+'；Qwen: '+qwen+'；其他: '+alts)+
            '</td><td>'+_html_escape(n['state'])+'</td><td><a href="'+_html_escape(n['crop_marked'])+ '" target="_blank">'+
            '<img loading="lazy" style="width:220px;max-height:150px;object-fit:contain" src="'+_html_escape(n['crop_marked'])+'" alt="目标节点裁剪"></a></td></tr>')
    content += ['</tbody></table>','<h2>原候选与自动节点对应关系</h2>',
                '<table><thead><tr><th>ID</th><th>原候选连接</th><th>节点</th><th>几何检查</th><th>相近读法建议</th></tr></thead><tbody>']
    for r in checked:
        link = r['link']
        suggestion = '；'.join(f"{role}→"+','.join(f"{p['candidate_name']} ({p['node_id']})" for p in ls)
                               for role,ls in link['suggestions'].items() if ls)
        content.append('<tr><td>'+_html_escape(r['candidate_id'])+'</td><td>'+_html_escape(str(r.get('start') or '?')+' → '+str(r.get('end') or '?'))+'</td><td>'+
            _html_escape(' / '.join(x or '未匹配' for x in link['node_ids']))+'</td><td>'+_html_escape(link['state'])+
            '</td><td>'+_html_escape(suggestion)+'</td></tr>')
    content += ['</tbody></table>',
                '<p>此页只展示自动候选；工程确认必须由人员核对原始线路和属性。',
                '原始 review.html、structured.xlsx 与 candidates.json 未被覆盖。</p></section>']
    insert = '\n'.join(content)
    if '</body>' in base:
        base = base.replace('</body>', insert+'\n</body>',1)
    else:
        base += insert
    (out/'review-with-nodes.html').write_text(base,encoding='utf-8')


def run(pdf: Path, out: Path, mode: str='qwen', max_nodes: int=40,
        model_id: str='Qwen/Qwen3.5-2B', lang: str='chi_sim+eng',
        existing_backend: Any=None, online: bool=False, replies: Path|None=None) -> dict[str, Any]:
    """Postprocess an existing Fast result; no modifications to Audit or base outputs."""
    import pymupdf
    pdf = Path(pdf).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    for path in ('geometry/geometry.json','candidates.json'):
        if not (out/path).is_file():
            raise FileNotFoundError(f'缺少 {out/path}，先运行 Fast v0.2')
    geom = json.loads((out/'geometry/geometry.json').read_text(encoding='utf-8'))
    ocr = json.loads((out/'ocr.json').read_text(encoding='utf-8')) if (out/'ocr.json').exists() else {'pages':{}}
    candidate_report = json.loads((out/'candidates.json').read_text(encoding='utf-8'))
    pages = geometry_pages(geom)
    total = sum(len(page.get('nodes',[])) for page in pages)
    if max_nodes <= 0 or total > max_nodes:
        raise ValueError(f'图中检测到 {total} 个节点；--max-nodes {max_nodes} 不够，请确认数量后增加上限')
    crop_root = out/'geometry/station_name_crops'
    crop_root.mkdir(parents=True,exist_ok=True)
    backend = existing_backend
    if mode == 'qwen' and replies is None and backend is None:
        backend = QwenNameReader(model_id=model_id,online=online)
    nodes_out = []
    with pymupdf.open(pdf) as doc:
        for page_geom in pages:
            pno = int(page_geom.get('page',1))
            if not (1<=pno<=len(doc)):
                raise ValueError(f'无效的几何页码 {pno}')
            pw,ph = page_dimensions(page_geom)
            page_ocr = ocr.get('pages',{}).get(str(pno),[])
            for index,node in enumerate(page_geom.get('nodes',[]),1):
                raw_file, marked_file, cropmeta = render_crop(doc[pno-1],node,crop_root,pno)
                crop_name = f"page{pno:03d}_{node['id']}"
                tesseract = tesseract_read(crop_root/raw_file,lang) if mode != 'crops' else []
                # Locate Tesseract lines close to the red-ring position (crop center),
                # avoiding random neighboring route annotations.
                mid_x = (cropmeta['center_pdf'][0]-cropmeta['pdf_bbox'][0])*4
                mid_y = (cropmeta['center_pdf'][1]-cropmeta['pdf_bbox'][1])*4
                near_crop = []
                for item in tesseract:
                    x0,y0,x1,y1=item['pixel_bbox']
                    dx = max(x0-mid_x,mid_x-x1,0)
                    dy = max(y0-mid_y,mid_y-y1,0)
                    if dx < 145 and dy < 125:
                        near_crop.append((math.hypot(dx,dy),item))
                near_crop.sort(key=lambda x:x[0])
                crop_ocr = [x[1] for x in near_crop[:2]]
                full_ocr = ocr_near_node(node,page_ocr,pw,ph)
                ocr_list = dedup_ocr_names(crop_ocr + full_ocr)
                reading: dict[str, Any] = {'name':None,'alternatives':[], 'raw':None,'error':None}
                if replies is not None:
                    src = replies/f'{crop_name}.txt'
                    if src.is_file():
                        raw = src.read_text(encoding='utf-8')
                        name, alternatives, error = clean_reply(raw)
                        reading = {'name':name,'alternatives':alternatives,'raw':raw,'error':error}
                elif mode == 'qwen' and backend is not None:
                    try:
                        if not isinstance(backend,QwenNameReader):
                            # Fast's CudaQwen has already loaded model/processor.
                            if not hasattr(backend, '_station_reader'):
                                backend._station_reader = QwenNameReader(existing=backend)
                            active = backend._station_reader
                        else:
                            active = backend
                        reading = active.read(crop_root/marked_file,index + pno*100)
                    except Exception as e:
                        reading = {'name':None,'alternatives':[],'raw':None,'error':f'{type(e).__name__}: {str(e)[:150]}'}
                record=node_candidate(node,ocr_list,reading,node.get('confirmed_name'))
                record.update({'page':pno,'center_pdf':cropmeta['center_pdf'],
                     'crop_raw':f'geometry/station_name_crops/{raw_file}',
                     'crop_marked':f'geometry/station_name_crops/{marked_file}',
                     'ocr_bbox_pdf':cropmeta['pdf_bbox']})
                nodes_out.append(record)
                print(f"p{pno} {node['id']} {record['primary_name'] or '?'} / {record['state']}", flush=True)
    checks=[]
    for record in candidate_report.get('records',[]):
        pno = int(record.get('page',1))
        pg = next((p for p in pages if int(p.get('page',1))==pno),{})
        link = link_record(record,[n for n in nodes_out if n['page']==pno],pg.get('edges',[]))
        checks.append({'candidate_id':record.get('id'),'page':pno,'start':record.get('start'),
                       'end':record.get('end'),'link':link})
    export_reports(out,pages,nodes_out,checks)
    return {'nodes':len(nodes_out),'with_primary_name':sum(bool(n['primary_name']) for n in nodes_out),
            'candidate_records':len(checks),
            'with_direct_edge':sum(bool(c['link']['edge_id']) for c in checks),
            'result_html':str(out/'review-with-nodes.html')}


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdf',type=Path)
    ap.add_argument('result_dir',type=Path,help='现有 Fast v0.2 输出目录')
    ap.add_argument('--mode',choices=['qwen','ocr','crops'],default='qwen',help='qwen=模型+OCR; ocr=只用OCR; crops=只生成裁图')
    ap.add_argument('--model-id',default='Qwen/Qwen3.5-2B')
    ap.add_argument('--online',action='store_true',help='允许下载；默认只用本地缓存')
    ap.add_argument('--max-nodes',type=int,default=40)
    ap.add_argument('--ocr-lang',default='chi_sim+eng')
    ap.add_argument('--replies-dir',type=Path,help='离线回放每个节点的JSON原始回复，格式 page001_N001.txt')
    args=ap.parse_args()
    try:
        result=run(args.pdf,args.result_dir,mode=args.mode, max_nodes=args.max_nodes,
                   model_id=args.model_id,lang=args.ocr_lang,online=args.online,replies=args.replies_dir)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as e:
        print(f'错误：{type(e).__name__}: {e}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())