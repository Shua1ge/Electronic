"""Render frozen-run metrics and independent source audits into a local report."""
import json
import sys
from html import escape as esc
from pathlib import Path
from full_pdf import save,sha256
from document_core import ROOT

out=Path(sys.argv[1]).resolve();validation=json.loads((out/'validation.json').read_text())
assert validation['status']=='finished' and len(validation['documents'])==3
assert all(sha256(ROOT/'scripts'/name)==digest for name,digest in validation['code'].items()),'Inference code drifted'
summary={'scope':'三份PDF全部79页处理；语义评价限定于31项独立事实检查和52条源业务图通道，非全文件准确率。','documents':[]}
parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>通信图纸 · 跨文件验证</title>',
'''<style>body{font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#24313c;background:#f4f6f8;margin:0}main{max-width:1150px;margin:auto;padding:40px 24px}h1{font-size:30px}h2{font-size:23px;margin-top:38px}a{color:#1463a0}table{border-collapse:collapse;width:100%;background:white;margin:18px 0}th,td{padding:10px 13px;text-align:left;border:1px solid #dbe2e8;vertical-align:top}th{background:#eaf0f5}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#5b6873}.note{background:#fff5dc;border-left:4px solid #c98b12;padding:16px}.card{background:white;padding:20px;margin:20px 0;border:1px solid #dbe2e8}.ok{color:#17673d}.bad{color:#a23a2a}summary{cursor:pointer;font-weight:600}.gallery{display:grid;grid-template-columns:1fr 1fr;gap:16px}.gallery img{width:100%;border:1px solid #ddd}@media(max-width:750px){.gallery{grid-template-columns:1fr}main{padding:20px 12px}}</style><main>''',
'<h1>通信施工图纸：跨文件验证</h1>',
'<p>同一个本地 Qwen3.5-4B 4bit 模型；本地 OCR、自动旋转、自动分段与连线定位。三份文件运行前固定识别代码，核对答案没有输入识别流程。</p>',
'<p class="note">页处理完成仅表示既定任务运行完成，不表示图纸已经完整识别。下面分别报告局部事实、源图业务通道和端点匹配；不把这些指标称为全文件准确率。</p>',
'<table><tr><th>文件</th><th>页任务完成</th><th>局部事实通过</th><th>通道严格通过</th><th>端点配对通过</th><th>业务图输出候选</th><th>端到端耗时</th></tr>']
labels={'sample-b':'项目B','sample-c':'项目C','sample-d':'项目D'}
for doc in validation['documents']:
 name=doc['name'];p=out/name;run=json.loads((p/'run.json').read_text());facts=json.loads((p/'audit/results.json').read_text());channels=json.loads((p/'audit/channel-results.json').read_text())
 pages=[json.loads(f.read_text()) for f in sorted((p/'pages').glob('*.json'))]
 assert len(pages)==doc['expected_pages']
 row={'name':name,'label':labels[name],'pages':len(pages),'completed_pages':run['successful_pages'],
      'fact_checks':facts['check_count'],'fact_matches':facts['final_complete_matches'],
      'source_channels':channels['expected_channels'],'strict_channel_matches':channels['strict_matches'],
      'endpoint_pair_matches':channels['endpoint_pair_matches'],'output_business_candidates':channels['output_candidates'],
      'wall_seconds':doc['wall_seconds'],'inference_seconds':run['inference_seconds'],'mlx_peak_gb':run['mlx_peak_gb'],
      'rotations':run.get('automatic_rotations',[]),'failed_pages':[p['page'] for p in pages if not p['page_complete']],
      'geometry':[{'page':p['page'],**p['geometry_coverage']} for p in pages if p.get('geometry_coverage')],
      'pdf_sha256':doc['pdf_sha256'],'review':str(p/'review.html')}
 summary['documents'].append(row)
 parts.append(f"<tr><td><a href='{name}/review.html'>{labels[name]}</a></td><td>{row['completed_pages']}/{row['pages']}</td><td>{row['fact_matches']}/{row['fact_checks']}</td><td>{row['strict_channel_matches']}/{row['source_channels']}</td><td>{row['endpoint_pair_matches']}/{row['source_channels']}</td><td>{row['output_business_candidates']}</td><td>{row['wall_seconds']/60:.1f} 分钟</td></tr>")
parts.append('</table><p>通道按原图逐条建立清单，一条候选只能匹配一条源通道。严格检查类别、两端及该通道已标注的关键业务属性；端点配对仅检查类别和站点，不代表属性正确。各图检查字段不同，不能简单用百分比比较难度。</p>')
parts.append('<p>项目A开发样例：原有相同20项局部检查由14项通过提高至19项；剩余地区城网10G的站点关联未通过。<a href="../sample-a_geometry/review.html">查看项目A回归</a>。</p>')
for row in summary['documents']:
 name=row['name'];p=out/name;protocol=json.loads((p/'audit/protocol.json').read_text());channels=json.loads((p/'audit/channel-results.json').read_text());facts=json.loads((p/'audit/results.json').read_text())
 parts.append(f"<h2>{row['label']}</h2><p><a href='{name}/review.html'>逐页完整报告</a> · <a href='{name}/structured.xlsx'>Excel</a> · <a href='{name}/structured.json'>JSON</a> · <a href='{name}/audit/channel-results.json'>逐通道评分</a> · <a href='{name}/audit/protocol.json'>核对定义</a></p>")
 parts.append('<div class="card">'+''.join(f'<p>{esc(note)}</p>' for note in protocol['notes'])+'</div>')
 parts.append('<details open><summary>源图通道逐项核对</summary><table><tr><th>原图通道</th><th>两端</th><th>严格检查</th><th>仅端点检查</th></tr>')
 for c in channels['source_channels']:
  match=channels['strict_assignment'].get(c['id']);endpoint=channels['endpoint_assignment'].get(c['id']);page=c['source_pages'][0]
  parts.append(f"<tr><td><a href='{name}/review.html#p{page}'>{esc(c['description'])}</a></td><td>{esc(' ↔ '.join(c['endpoints']))}</td><td class='{'ok' if match else 'bad'}'>{esc(match or '未通过')}</td><td>{esc(endpoint or '未通过')}</td></tr>")
 parts.append('</table></details><details><summary>局部事实检查</summary><table><tr><th>检查</th><th>结果</th></tr>')
 for c in facts['checks']:parts.append(f"<tr><td>{esc(c['description'])}</td><td>{esc(', '.join(c['final_matching_ids']) or '未通过')}</td></tr>")
 parts.append('</table></details>')
 for page in sorted({n for c in channels['source_channels'] for n in c['source_pages']}):
  parts.append(f"<div class='gallery'><a href='audit-source/{name}-{page:03d}.png'><img src='audit-source/{name}-{page:03d}.png' alt='源图业务页'></a>")
  geometry=p/'assets'/f'{page:03d}'/'channels/geometry.png'
  if geometry.exists():parts.append(f"<a href='{name}/assets/{page:03d}/channels/geometry.png'><img src='{name}/assets/{page:03d}/channels/geometry.png' alt='自动检测连线'></a>")
  parts.append('</div>')
summary['total_pages']=sum(r['pages'] for r in summary['documents'])
summary['completed_pages']=sum(r['completed_pages'] for r in summary['documents'])
summary['fact_matches']=sum(r['fact_matches'] for r in summary['documents'])
summary['fact_checks']=sum(r['fact_checks'] for r in summary['documents'])
summary['source_channels']=sum(r['source_channels'] for r in summary['documents'])
summary['strict_channel_matches']=sum(r['strict_channel_matches'] for r in summary['documents'])
parts.append('<h2>范围与可复核性</h2><p>原图标注与评价由助手完成，仍需通信专业人员复核。三份样本均为相关工程图纸，不能据此证明对其他设计院版式、低清扫描、污损或拍照图泛化。源文件、模型版本、代码快照、每次原始回答与字段修正均保留。</p><p><a href="validation.json">冻结版本与运行记录</a> · <a href="summary.json">汇总指标</a>。本轮没有训练或更换模型，也没有把验证集的站名或答案加入推理规则。</p></main></html>')
save(out/'summary.json',summary);(out/'review.html').write_text('\n'.join(parts))
print(json.dumps(summary,ensure_ascii=False,indent=2))
