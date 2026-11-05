"""Render an offline validation report from completed runs and independent audits."""
import html,json,sys
from pathlib import Path
from full_pdf import save
from document_core import missing_value

def main():
    out=Path(sys.argv[1]);state=json.loads((out/'validation.json').read_text())
    esc=lambda x:html.escape(str(x))
    rows=[];sections=[];summaries=[]
    labels={'sample-b':'项目B','sample-c':'项目C','sample-d':'项目D'}
    for entry in state['documents']:
        name=entry['name'];folder=out/name
        run=json.loads((folder/'run.json').read_text());facts=json.loads((folder/'audit/results.json').read_text())
        channels=json.loads((folder/'audit/channel-results.json').read_text());protocol=json.loads((folder/'audit/protocol.json').read_text())
        pages=[json.loads(p.read_text()) for p in sorted((folder/'pages').glob('*.json'))]
        channel_calls=list((folder/'channel-calls').rglob('C*.json'));empty_channel_answers=0
        for call in channel_calls:
            obj=json.loads(json.loads(call.read_text())['raw'])
            if not any(any(not missing_value(v) for v in r['fields'].values()) for r in obj.get('records',[])):empty_channel_answers+=1
        counts={'document':name,'label':labels[name],'pages':len(pages),'page_tasks_complete':run['successful_pages'],
                'facts_matched':facts['final_complete_matches'],'facts_total':facts['check_count'],
                'source_channels':channels['expected_channels'],'business_candidates':channels['output_candidates'],
                'channels_matched':channels['strict_matches'],'endpoint_pairs_matched':channels['endpoint_pair_matches'],
                'withheld_fields':run['withheld_fields'],'wall_seconds':entry['wall_seconds'],
                'document_business_candidates':sum(run['category_counts'].get(c,0) for c in ['保护业务','非保护业务']),
                'channel_model_calls':len(channel_calls),'raw_empty_channel_answers':empty_channel_answers,
                'inference_seconds':run['inference_seconds'],'mlx_peak_gb':run['mlx_peak_gb'],
                'unfinished_pages':[p['page'] for p in pages if not p['page_complete']],
                'rotations':run.get('automatic_rotations',[]),
                'geometry_pages':[{'page':p['page'],**p['geometry_coverage']} for p in pages if p.get('geometry_coverage')]}
        summaries.append(counts)
        rows.append(f"<tr><td><a href='#{name}'>{labels[name]}</a></td><td>{counts['page_tasks_complete']}/{len(pages)}</td><td>{counts['facts_matched']}/{counts['facts_total']}</td><td>{counts['channels_matched']}/{counts['source_channels']}</td><td>{counts['endpoint_pairs_matched']}/{counts['source_channels']}</td><td>{counts['business_candidates']}</td><td>{counts['wall_seconds']/60:.1f}</td></tr>")
        parts=[f"<section id='{name}'><h2>{labels[name]}</h2><p><a href='{name}/review.html'>逐页图文对照</a> · <a href='{name}/structured.xlsx'>Excel</a> · <a href='{name}/structured.json'>JSON</a> · <a href='{name}/audit/protocol.json'>原图核对定义</a> · <a href='{name}/audit/channel-results.json'>逐线匹配明细</a></p>"]
        parts.append(f"<p>模型调用 {counts['inference_seconds']/60:.1f} 分钟，MLX 分配峰值 {counts['mlx_peak_gb']:.2f} GB；未完成页 {esc(counts['unfinished_pages'])}。累计 {counts['withheld_fields']} 个字段因缺少文字证据等原因被隔离。</p>")
        parts.append(f"<p>全文件共有 {counts['document_business_candidates']} 条保护/非保护业务候选，可能重复或错误。此处通道评分限定原业务图页，不计说明页或其他页可能重复描述的记录；0分不表示全文完全没有业务候选。</p>")
        if channel_calls:parts.append(f"<p>{len(channel_calls)} 次逐线模型回答中，{empty_channel_answers} 次原始字段全空或没有记录。后处理可以补入几何端点，故端点匹配不等于模型已理解业务。</p>")
        parts.append('<ul>'+''.join(f'<li>{esc(note)}</li>' for note in protocol.get('notes',[]))+'</ul>')
        manual=folder/'audit/manual-findings.json'
        if manual.exists():
            parts.append('<h3>对照原图确认的问题</h3><ul>'+''.join(f"<li>第 {item['source_page']} 页：{esc(item['description'])}</li>" for item in json.loads(manual.read_text())['findings'])+'</ul>')
        parts.append('<details><summary>事实检查逐项结果</summary><table><tr><th>核对项</th><th>来源页</th><th>命中记录</th></tr>')
        for c in facts['checks']:parts.append(f"<tr><td>{esc(c['description'])}</td><td>{esc(c['source_pages'])}</td><td>{esc(c['final_matching_ids'])}</td></tr>")
        parts.append('</table></details><details open><summary>业务通道一对一核对</summary><table><tr><th>原图业务</th><th>完整匹配记录</th><th>仅端点匹配记录</th></tr>')
        for c in channels['source_channels']:
            parts.append(f"<tr><td>{esc(c['description'])}</td><td>{esc(channels['strict_assignment'].get(c['id'],'未完整匹配'))}</td><td>{esc(channels['endpoint_assignment'].get(c['id'],'未匹配'))}</td></tr>")
        parts.append('</table></details></section>');sections.append(''.join(parts))
    conclusion={'scope':'冻结代码后另外三份PDF的完整运行；局部事实抽查和业务图逐线核对；非全文件准确率，非领域专家签认。',
                'model':state['model'],'documents':summaries,'code_frozen':state['code'],
                'total_pages':sum(r['pages'] for r in summaries),'total_source_channels':sum(r['source_channels'] for r in summaries),
                'matched_channels':sum(r['channels_matched'] for r in summaries),
                'facts_matched':sum(r['facts_matched'] for r in summaries),'facts_total':sum(r['facts_total'] for r in summaries)}
    save(out/'summary.json',conclusion)
    body='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>通信图纸 · 固定版本泛化验证</title>
<style>body{max-width:1240px;margin:36px auto;padding:0 24px;font:16px/1.65 system-ui;color:#20303a;background:#f7f8fa}h1,h2{line-height:1.25}section{background:white;border:1px solid #d6dce1;padding:24px;margin-top:24px;border-radius:8px}table{border-collapse:collapse;width:100%;background:white;font-size:14px}td,th{border:1px solid #d6dce1;padding:10px;text-align:left}th{background:#eaf0f4}a{color:#17619b}summary{cursor:pointer;font-weight:600;margin:14px 0}.notice{padding:18px;border-left:4px solid #b66b17;background:#fff1d9}</style>
<h1>通信图纸 · 固定版本泛化验证</h1>
<p>相同 Qwen3.5-4B / MLX 4bit、本地 Apple Vision OCR；几何连线与字段约束由程序执行。Python 3.13，全部推理位于禁止网络的本地子进程。没有人工给模型选择页面或裁剪区域，也没有训练或更换模型。</p>
<p>开发集项目A：同一20项检查由14项提高到19项。业务图的测距、调度终点及IAD有所改善，地区城网10G完整关联和重复光路仍未解决。<a href="../sample-a_geometry/review.html">项目A回归结果</a></p>
<p class="notice">页任务完成、JSON合法和检测到连线，均不代表事实正确。下面的通道得分要求同一条记录同时满足原图规定的端点、业务区分、速率及适用的型号/系统；采用一对一匹配，重复候选不能重复得分。仅端点得分不验证业务属性。三份是相关通信工程图纸，不能外推到任意图纸或有噪声的扫描件。</p>
<p><a href="validation.json">运行与冻结代码指纹</a> · <a href="summary.json">统计JSON</a> · <a href="frozen-source/run_document.py">入口代码快照</a></p>
<table><tr><th>文件</th><th>页任务完成</th><th>事实抽查</th><th>通道完整匹配</th><th>仅端点匹配</th><th>业务图候选数</th><th>全程分钟</th></tr>'''
    body+=''.join(rows)+'</table>'
    body+='<p class="notice">结论：开发样例的改善未稳定迁移到另外三份图纸。当前流程仍需逐条复核，不能自动交付业务表。项目C第24页、项目D第27页安全说明已读取，但归为“其他”并保留待复核，故页任务完成数少于已处理页数。</p>'
    if (out.parent/'generalization_safe/replay.json').exists():
        body+='<p><a href="../generalization_safe/review.html">查看验证后修正版</a>：修复OCR数字截取、章节误拆及通道图分类，项目D第1、7页重跑，其余页复用。三份通道关键字段得分未改变；此修正不算新的独立泛化验证。</p>'
    body+=''.join(sections)+'</html>'
    (out/'review.html').write_text(body)
    print(json.dumps(conclusion,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
