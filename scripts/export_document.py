"""Export model candidates, with provenance and explicit unresolved fields."""
import html
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from document_core import FIELDS


def literal(sheet, values):
    values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict,list)) else v for v in values]
    sheet.append(values)
    # Source/model text must never become an Excel formula.
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"


def style(sheet):
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for row in sheet:
        for cell in row:
            cell.font = Font(name="Arial", size=11)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="16324F")
        cell.font = Font(name="Arial", size=11, color="FFFFFF", bold=True)
    for col in range(1, sheet.max_column+1):
        sheet.column_dimensions[get_column_letter(col)].width = 22 if col > 1 else 12
    sheet.row_dimensions[1].height = 34
    for row in range(2, sheet.max_row+1):
        sheet.row_dimensions[row].height = 52


def export_run(out):
    out = Path(out)
    manifest = json.loads((out / "run.json").read_text())
    structured = json.loads((out / "structured.json").read_text())
    observations = json.loads((out / "observations.json").read_text())
    pages = [json.loads(p.read_text()) for p in sorted((out / "pages").glob("*.json"))]
    evidence_mode = manifest.get("automatic_crops", False)
    workbook = Workbook()
    notes = workbook.active
    notes.title = "说明"
    for row in [["项目", "说明"], ["输入PDF", manifest["input"]],
                ["状态", "本文件全部为模型候选结果，未经人工确认，不可直接作为业务申请依据。"],
                ["空白含义", "来源未识别/没有依据或合并存在冲突；空白不表示原图一定没有。"],
                ["字段来源", "业务列名来自通信图纸识别.pptx；本地4B模型提取，辅以本地OCR和几何连线约束。原始回答与字段修正分别保留。"],
                ["合并方式", "仅对逐字相同的项目元数据去重；业务候选保留，不自动合并。" if evidence_mode else "模型只提出记录ID分组；程序合并原始字段，冲突留空并列出候选值。"],
                ["并行线路", "站点相同不能证明是同一条光缆/光路，候选分组仍需核对。"],
                ["校验范围", "完成状态和JSON合法性不代表事实完整或准确。"],
                ["复核建议", "先核对来源页与证据，再核对型号、芯数、数量、单位、端口与中途节点。"]]:
        literal(notes, row)
    style(notes)
    notes.column_dimensions["B"].width = 110
    if evidence_mode:
        literal(notes,["证据坐标","矩形为0..1归一化xyxy，左上为原点；自动旋转页以标准方向整页为坐标基准，原坐标保存在OCR JSON的original_bbox中。"])
        literal(notes,["提取范围","当前提取各类8—9个核心字段；具体端口、有效截止日期等扩展字段未覆盖。空白不表示原图没有。"])
    for category, fields in FIELDS.items():
        sheet = workbook.create_sheet(category)
        literal(sheet, ["序号"] + fields + ["记录ID", "来源页", "来源记录", "状态", "冲突", "证据原文"])
        for number, row in enumerate([r for r in structured["records"] if r["category"] == category], 1):
            literal(sheet, [number] + [row["fields"].get(f) for f in fields] +
                    [row["id"], ",".join(map(str,row["source_pages"])), ",".join(row["source_ids"]),
                     row["status"], json.dumps(row["conflicts"],ensure_ascii=False) if row["conflicts"] else None,
                     "\n".join(f"p{e['page']} {e['text']}" for e in row["evidence"])])
            for column, field in enumerate(fields, 2):
                sheet.cell(sheet.max_row,column).comment = Comment(
                    f"PDF: {manifest['input']}\n来源记录: {','.join(row['field_sources'].get(field, [])) or '无确定值'}\n来源页: {row['source_pages']}\n所有值均待核对。", "Local extraction")
        style(sheet)
        sheet.column_dimensions[get_column_letter(sheet.max_column)].width = 70
    sheet = workbook.create_sheet("逐页原始记录")
    literal(sheet, ["记录ID","页码","类别","字段JSON","证据","不确定项"])
    for row in observations:
        literal(sheet,[row["id"],row["page"],row["category"],json.dumps(row["fields"],ensure_ascii=False),row["evidence"],"; ".join(row["uncertainties"])])
    style(sheet)
    geometric=[r for r in observations if r.get('channel_id')]
    if geometric:
        sheet=workbook.create_sheet('连线定位与修正')
        literal(sheet,['记录ID','页码','连线ID','端点及连线JSON','字段修正JSON'])
        for row in geometric:
            literal(sheet,[row['id'],row['page'],row['channel_id'],json.dumps(row['geometry'],ensure_ascii=False),json.dumps(row.get('geometry_field_changes',[]),ensure_ascii=False)])
        style(sheet)
    sheet.column_dimensions["D"].width = 95
    sheet.column_dimensions["E"].width = 75
    sheet = workbook.create_sheet("候选跨页关联")
    literal(sheet,["来源记录","目标记录","关系","依据","状态"])
    for link in structured["links"]:
        literal(sheet,[link.get(k) for k in ("from_id","to_id","relation","reason","status")])
    style(sheet)
    sheet = workbook.create_sheet("页处理状态")
    literal(sheet,["页码","页面类型","图名","结构解析","原始记录数","未解决项","模型耗时秒"])
    for page in pages:
        e = page["extraction"] or {}
        literal(sheet,[page["page"],e.get("page_kind"),e.get("title"),"通过" if page.get("page_complete",bool(e)) else "存在未完成项",
                       len(e.get("records",[])),"; ".join(e.get("unresolved",[])),round(sum(a["seconds"] for a in page["attempts"]),2)])
    style(sheet)
    if evidence_mode:
        sheet=workbook.create_sheet("字段证据坐标")
        literal(sheet,["来源记录","页码","字段","候选值","OCR行ID","检查方式","OCR原文","归一化矩形xyxy"])
        for row in observations:
            for key,check in row.get("field_evidence",{}).items():
                literal(sheet,[row["id"],row["page"],key,row["fields"][key],check["source_ids"],check["check"],
                               [s["text"] for s in check["sources"]],[s["bbox"] for s in check["sources"]]])
        style(sheet)
        sheet.column_dimensions["G"].width=80
        sheet=workbook.create_sheet("未核实字段")
        literal(sheet,["来源记录","页码","字段","模型原值","未采纳原因","OCR行ID"])
        for row in observations:
            for key,value in row.get("withheld_fields",{}).items():
                literal(sheet,[row["id"],row["page"],key,value["value"],value["reason"],value.get("citations",[])])
        for page in pages:
            for rejected in page.get("rejected_records",[]):
                literal(sheet,["未进入候选的记录",page["page"],None,rejected.get("raw"),rejected["reason"],None])
        style(sheet)
        sheet.column_dimensions["D"].width=80
    manual_path = out / "audit/manual-findings.json"
    manual = json.loads(manual_path.read_text()) if manual_path.exists() else {"findings": []}
    if manual["findings"]:
        sheet = workbook.create_sheet("助手抽查问题")
        literal(sheet,["问题ID","来源页","原始记录","问题类型","核对说明","已确认记录数","已确认字段数"])
        for item in manual["findings"]:
            literal(sheet,[item["id"],item["source_page"],",".join(item["record_ids"]),item["type"],item["description"],item.get("confirmed_record_count"),item.get("confirmed_field_count")])
        style(sheet)
        sheet.column_dimensions["E"].width=100
    audit_path = out / "audit/results.json"
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
    comparison_path=out/"audit/comparison.json"
    comparison=json.loads(comparison_path.read_text()) if comparison_path.exists() else None
    if comparison:
        sheet=workbook.create_sheet("版本对比")
        literal(sheet,["检查ID","检查内容","上一版命中ID","本版命中ID","变化"])
        for row in comparison["rows"]:
            literal(sheet,[row["id"],row["description"],row["before_ids"],row["after_ids"],row["change"]])
        style(sheet)
        sheet.column_dimensions["B"].width=85
    if audit:
        sheet=workbook.create_sheet("关键事实检查")
        literal(sheet,["检查ID","原图依据页","检查内容","原始输出命中ID","最终候选命中ID","结论范围"])
        for item in audit["checks"]:
            literal(sheet,[item["id"],str(item["source_pages"]),item["description"],",".join(item["raw_matching_ids"]),",".join(item["final_matching_ids"]),"抽查检查项；不是全文件准确率"])
        style(sheet)
        sheet.column_dimensions["C"].width=75
        recovered=json.loads((out/"audit/raw-readable.json").read_text())
        sheet=workbook.create_sheet("失败页可读片段")
        literal(sheet,["来源记录","页码","类别","原始字段JSON","证据","注意"])
        for row in recovered:
            if not row["included_in_strict_pipeline"]:
                literal(sheet,[row["id"],row["page"],row["category"],json.dumps(row["fields"],ensure_ascii=False),row.get("evidence"),"仅供审计；可能是截断前的片段，未补入最终候选"])
        style(sheet)
        sheet.column_dimensions["D"].width=100
    context_path=out/'engineering/context.json'
    if context_path.exists():
        context=json.loads(context_path.read_text());sheet=workbook.create_sheet('工程上下文陈述')
        literal(sheet,['陈述ID','状态','阶段','来源页','原文','证据JSON'])
        for c in context['claims']:literal(sheet,[c['id'],c['status'],c['stage'],c['source']['page'],c['text'],c['source']])
        style(sheet)
    tasks_path=out/'engineering/review-tasks.json'
    if tasks_path.exists():
        sheet=workbook.create_sheet('定向复查任务');literal(sheet,['任务序号','类型','来源页','问题','状态','任务JSON'])
        for i,t in enumerate(json.loads(tasks_path.read_text())):literal(sheet,[i,t['kind'],t['page'],t['question'],t['status'],t])
        style(sheet)
    workbook.save(out / "structured.xlsx")
    check = load_workbook(out / "structured.xlsx")
    assert set(FIELDS) <= set(check.sheetnames)
    assert not any(c.data_type == "f" for s in check for row in s for c in row)
    assert sum(check[c].max_row-1 for c in FIELDS) == len(structured["records"])

    esc = html.escape
    parts = ["""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>整份PDF识别复核</title><link rel="icon" href="data:,"><style>
body{font:15px/1.65 system-ui,sans-serif;margin:32px auto;max-width:1320px;padding:0 20px;color:#192b3b;background:#f6f8fa}
h1,h2{line-height:1.3} a{color:#1259a7} .notice{background:#fff1ce;padding:18px;border-radius:8px}
.card{background:white;padding:20px;margin:20px 0;border:1px solid #dce3e9;border-radius:10px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px} img{width:100%;height:auto;border:1px solid #ddd}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px} table{width:100%;border-collapse:collapse}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}
.tag{display:inline-block;background:#e7edf4;padding:2px 8px;border-radius:4px}details{margin:10px 0} @media(max-width:900px){.grid{grid-template-columns:1fr}}
</style><h1>整份 PDF · 4B 自动识别基线</h1>"""]
    if context_path.exists():
        parts.append("<div class='notice'>工程上下文为待验证原文陈述，不是已确认事实。对象与图纸表达分别保存，不自动合并。<p><a href='engineering/context.json'>施工说明陈述</a></p></div>")
        if (out/'engineering/ontology-task-plan.json').exists():parts.append("<p><a href='engineering/ontology-task-plan.json'>按本体约束的段落任务计划（尚未执行语义抽取）</a></p>")
        if (out/'engineering/graph.json').exists():parts.append("<p><a href='engineering/graph.json'>对象、表达与身份判定</a> · <a href='engineering/review-tasks.json'>定向复查任务</a></p>")
        if (out/'engineering/review-results.json').exists():parts.append("<p><a href='engineering/review-results.json'>模型定向复查回答（未覆盖事实）</a></p>")
    coverage = "全部页面已记录" if len(pages)==manifest['page_count'] else f"仅记录 {len(pages)}/{manifest['page_count']} 页；这是未完成的诊断运行"
    method="整页概览＋自动细节图/章节区域/图形组＋本地OCR；无人工裁剪。当前按类别提取核心字段，具体端口等扩展字段尚未覆盖。仅去重项目元数据，保留业务记录。字段引用OCR不代表已验证连线归属。" if evidence_mode else "输入为自动整页图像，无人工裁剪。"
    parts.append(f"<p>{esc(Path(manifest['input']).name)}</p><div class='notice'><b>这是待复核结果。</b>{coverage}，运行状态：{esc(manifest['status'])}。{method}程序完成、JSON合法不等于识别完整或准确。</div>")
    parts.append(f"<p>{manifest['successful_pages']}/{manifest['page_count']} 页计划步骤通过；{manifest['observations']} 条逐页候选记录；{manifest['output_records']} 条最终候选记录；{manifest['conflicted_records']} 条存在字段冲突。</p>")
    if evidence_mode:
        parts.append(f"<p>未核实字段 {manifest.get('withheld_fields',0)} 个，原值另存供复核，不直接填入业务表。全部原始模型回答和被拒绝记录仍保留。</p>")
        parts.append(f"<p>明确原文的列映射 {manifest.get('explicit_column_mappings',0)} 项；补充唯一文字引用 {manifest.get('supplemented_citations',0)} 项。列映射只整理已读到的文字；补引用不证明关系正确。<a href='field-normalizations.json'>列映射记录</a></p>")
    parts.append("<p><a href='structured.xlsx'>下载结构化 Excel</a> · <a href='structured.json'>结构化 JSON</a> · <a href='run.json'>运行参数</a> · <a href='observations.json'>全部原始记录</a></p>")
    if audit:
        stage="经过证据检查的逐页候选" if evidence_mode else "可恢复的原始回答"
        parts.append(f"<section class='card'><h2>独立抽查</h2><p>{audit['check_count']} 项关键事实检查：{stage}完整命中 {audit['raw_complete_matches']} 项，最终候选记录完整命中 {audit['final_complete_matches']} 项。这不是全文件准确率；失败页中的完整JSON对象只用于审计，没有悄悄补入正式候选输出。</p><p><a href='audit/results.json'>检查结果</a> · <a href='audit/protocol.json'>检查定义</a></p></section>")
    if comparison:
        parts.append(f"<section class='card'><h2>同一组检查的版本对比</h2><p>上一版最终候选命中 {comparison['baseline_matches']}/{comparison['check_count']}；本版命中 {comparison['current_matches']}/{comparison['check_count']}。{esc(comparison['scope'])}</p><details><summary>逐项对照</summary><table><tr><th>检查</th><th>上一版</th><th>本版</th></tr>")
        for row in comparison["rows"]:
            parts.append(f"<tr><td>{esc(row['description'])}</td><td>{'命中' if row['before_ids'] else '未命中'}</td><td>{'命中' if row['after_ids'] else '未命中'}</td></tr>")
        parts.append("</table></details></section>")
    if manual["findings"]:
        parts.append("<section class='card'><h2>助手已对照原图确认的问题</h2><p>下面是局部抽查，仍需业务人员复核；没有修改模型原始答案。</p><table><tr><th>来源</th><th>问题</th></tr>")
        for item in manual["findings"]:
            parts.append(f"<tr><td><a href='#p{item['source_page']}'>第 {item['source_page']} 页</a><br>{esc(','.join(item['record_ids']))}</td><td><b>{esc(item['type'])}</b><br>{esc(item['description'])}</td></tr>")
        parts.append("</table></section>")
    parts.append("<p>页码："+" ".join(f"<a href='#p{p['page']}'>{p['page']}</a>" for p in pages)+"</p>")
    for page in pages:
        n=page["page"]; e=page["extraction"]
        parts.append(f"<section class='card' id='p{n}'><h2>第 {n} 页 · {esc((e or {}).get('title') or '未识别图名')}</h2><div class='grid'><a href='pages/{n:03d}.png'><img loading='lazy' src='pages/{n:03d}.png' alt='第{n}页完整原图'></a><div>")
        parts.append(f"<span class='tag'>{esc((e or {}).get('page_kind','结构解析失败'))}</span>")
        if evidence_mode:
            parts.append(f"<p><a href='assets/{n:03d}/ocr.png'>高清整页</a> · <a href='assets/{n:03d}/ocr.json'>OCR文字及坐标</a> · "+" · ".join(f"<a href='assets/{n:03d}/detail-{i}.png'>自动细节{i}</a>" for i in range(1,5))+"</p>")
            sections=sorted((out/"assets"/f"{n:03d}").glob("section-*.png"))
            if sections:parts.append("<p>自动章节区域："+" · ".join(f"<a href='{esc(str(p.relative_to(out)))}'>{esc(p.stem)}</a>" for p in sections)+"</p>")
            groups=sorted((out/'assets'/f'{n:03d}'/'groups').glob('region-*.png'))
            if page.get('geometry_coverage'):
                coverage=page['geometry_coverage']
                parts.append(f"<p>几何候选连线 {coverage['detected_channels']} 条，其中 {coverage['with_records']} 条有记录；未绑定线段 {coverage['unbound_lines']}，未覆盖业务文字 {coverage['uncovered_business_labels']}。这些是覆盖统计，不是准确率。<a href='assets/{n:03d}/channels/geometry.png'>连线叠加图</a> · <a href='assets/{n:03d}/channels/coverage.json'>覆盖明细</a> · <a href='assets/{n:03d}/channels/geometry.json'>坐标和OCR依据</a></p>")
            if groups:parts.append('<p>自动连通图形组：'+' · '.join(f"<a href='{esc(str(p.relative_to(out)))}'>{esc(p.stem)}</a>" for p in groups)+f" · <a href='assets/{n:03d}/groups/regions.json'>分区坐标与OCR</a></p>")
            if page.get('rotation_ccw'):
                parts.append(f"<p>根据文字方向自动逆时针旋转 {page['rotation_ccw']}°。<a href='assets/{n:03d}/unrotated-ocr.png'>旋转前整页</a>；字段坐标对应旋转后图像。</p>")
            if not page.get("page_complete"):parts.append("<p class='notice'>本页存在分类或提取未完成项，需复核。</p>")
        if e:
            for k,row in enumerate(e["records"],1):
                parts.append(f"<details open><summary><b>P{n:03d}R{k:03d} · {esc(row['category'])}</b></summary><table>")
                for key,value in row["fields"].items():
                    parts.append(f"<tr><th>{esc(key)}</th><td>{esc(str(value))}</td></tr>")
                parts.append(f"</table><p>模型所述证据：{esc(row['evidence'])}</p><p>不确定项：{esc('; '.join(row['uncertainties']))}</p></details>")
            parts.append(f"<p>本页未解决：{esc('; '.join(e['unresolved']))}</p>")
        parts.append(f"<details><summary>原始模型输出与验证错误</summary><pre>{esc(json.dumps(page['attempts'],ensure_ascii=False,indent=2))}</pre></details></div></div></section>")
    parts.append("</html>")
    (out / "review.html").write_text("\n".join(parts))


if __name__ == "__main__":
    import sys
    export_run(Path(sys.argv[1]))
