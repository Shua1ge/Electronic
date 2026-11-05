"""Local evidence report for the sample-grounded ontology, without accuracy claims."""
import html,json,hashlib
from collections import Counter
from pathlib import Path
from engineering_ontology import CATALOG,validate_bundle
from ontology_extraction import paragraph_units,extraction_task
from full_pdf import ROOT,save

def main():
    out=ROOT/'outputs/ontology_discovery';inventory=json.loads((out/'inventory.json').read_text());pages=json.loads((out/'new-pages.json').read_text())
    cases=json.loads((out/'examples/index.json').read_text());dups=json.loads((out/'render-duplicates.json').read_text())
    plans=[]
    for p in pages:
        if '卷册说明' not in p['file'] and not ('鉴权' in p['file'] and p['page']==4):continue
        units=paragraph_units(p['lines']);tasks=[extraction_task(p,u) for u in units]
        f=out/'extraction-plans'/(p['document_sha256'][:16]+f'-{p["page"]:03}.json');save(f,tasks)
        plans.append({'file':p['file'],'page':p['page'],'paragraph_candidates':len(units),'plan':str(f.relative_to(out)),'model_executed':False})
    save(out/'extraction-plans/index.json',plans)
    summary={'catalog_version':CATALOG['version'],'files_total':len(inventory),'pages_total':sum(f['pages'] for f in inventory),
             'new_files_read':len({p['file'] for p in pages}),'new_pages_read':len(pages),
             'identical_render_groups':len(dups),'repeated_pages':sum(len(g['occurrences'])-1 for g in dups),
             'classes':len(CATALOG['classes']),'relations':len(CATALOG['relations']),'attributes':len(CATALOG['attributes']),'rules':len(CATALOG['rules']),
             'source_linked_examples':len(cases),'prepared_paragraph_tasks':sum(t['paragraph_candidates'] for t in plans),
             'model_extraction_executed':False,'independent_evaluation':False,'tests_passed':80,
             'limits':['OCR读取不等于逐页专家审图','六组案例为人工整理的建模示例，非模型识别成绩','当前旧业务输出为兼容投影，未完成全部严格声明包迁移']}
    save(out/'summary.json',summary)
    esc=lambda x:html.escape(str(x))
    body=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>通用工程本体 v0.3</title><style>body{font:16px/1.7 system-ui;max-width:1200px;margin:36px auto;padding:0 24px;color:#20323b}h1,h2{line-height:1.3}aside{padding:20px;background:#fff2d8;border-left:4px solid #a75e10}section{margin:24px 0;padding:20px;border:1px solid #d3dce0;border-radius:8px}table{border-collapse:collapse;width:100%}th,td{padding:9px;border:1px solid #d3dce0;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#116298}summary{cursor:pointer;font-weight:600}</style>
<h1>通信与ICT工程通用本体 v0.3</h1><p>通用工程内核 + 专业扩展 + 来源证据与判定</p>
<aside>已完成样例盘点、本地OCR读取、类型/关系目录、声明校验、六组建模示例与段落任务计划。尚未完成基于本体的通用模型抽取，不能据此宣称识别准确率提升。新资料已经参与设计，不属于独立测试集。</aside>''']
    body.append(f"<p>{summary['files_total']}份PDF、{summary['pages_total']}页；新增{summary['new_files_read']}份、{summary['new_pages_read']}页完成离线OCR。发现{len(dups)}组完全相同的单页/合订本渲染；重复{summary['repeated_pages']}页不重复计实体或独立证据。</p>")
    body.append('<p><a href="../../docs/engineering-ontology.md">完整架构文档</a> · <a href="../../config/engineering-ontology.json">机器可读本体</a> · <a href="../../config/engineering-statements.schema.json">声明JSON Schema</a> · <a href="inventory.json">原始文件清单</a> · <a href="render-duplicates.json">重复页对应</a> · <a href="tests.log">80项测试</a></p>')
    body.append('<h2>核心结构</h2><p>项目/责任/分期/阶段/版本 → 有来源的陈述 → 物理对象、逻辑对象、建设活动 → 类型与业务规则校验 → 身份判定和定向复查。图纸表达与工程对象分开，设备型号与实例分开，设计要求与完成事实分开。</p><table><tr><th>模块</th><th>对象</th></tr>')
    for profile,types in CATALOG['profiles'].items():body.append(f'<tr><td>{esc(profile)}</td><td>{esc("、".join(CATALOG["classes"][t]["label"] for t in types))}</td></tr>')
    body.append('</table><h2>六组有来源的建模示例</h2>')
    for c in cases:
        image=Path(c['image']).relative_to(out);result=validate_bundle(json.loads((out/c['bundle']).read_text()));assert result['valid']
        body.append(f'<section><h3>{esc(c["title"])}</h3><p>{esc(c["lesson"])}</p><p><b>禁止：</b>{esc(c["prohibited"])}</p><p>来源：{esc(c["file"])}，第{c["page"]}页。<a href="{esc(image)}">查看原图</a> · <a href="{esc(c["bundle"])}">带来源的结构化示例</a></p><details><summary>OCR原文片段（保留误读，不作为专家签认）</summary><pre>{esc(c["source_quote"])}</pre></details></section>')
    body.append('<h2>规则及反例</h2><table><tr><th>规则</th><th>判定依据</th><th>禁止操作</th><th>反例</th></tr>')
    for r in CATALOG['rules']:body.append(f'<tr><td>{esc(r["id"]+" "+r["name"])}</td><td>{esc(r["requires"])}</td><td>{esc(r["forbidden"])}</td><td>{esc(r["counterexample"])}</td></tr>')
    body.append(f'</table><h2>说明正文的下一步抽取接口</h2><p>已为六份说明正文生成{summary["prepared_paragraph_tasks"]}个段落候选任务。每个任务携带原文行、位置、专业模块和允许的类型/关系，不含审计答案。当前状态为“准备完成、模型未执行”。表格行列关系另行处理，不能线性拼接OCR。</p><p><a href="extraction-plans/index.json">任务清单</a> · <a href="summary.json">范围及统计</a></p></html>')
    (out/'review.html').write_text('\n'.join(body));print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':main()
