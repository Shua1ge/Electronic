"""Report the scope of the v2 development replay, distinct from generalization."""
import html,json,sys
from pathlib import Path

def main():
    out=Path(sys.argv[1]);rows=[];summaries=[]
    for name,label,old in [('sample-a','项目A',16),('sample-b','项目B',0),('sample-c','项目C',23),('sample-d','项目D',10)]:
        g=json.loads((out/name/'geometry/geometry.json').read_text())
        d={'name':name,'label':label,'previous_geometric_candidates':old,'geometric_candidates':len(g['channels']),
           'unknown_category':sum(c['category'] is None for c in g['channels']),
           'folded_paths':sum(c['path_kind']=='orthogonal_fold' for c in g['channels']),
           'legend_associations':sum(bool(c['legend_evidence']) for c in g['channels']),
           'not_business_accuracy':True};summaries.append(d)
        rows.append(f"<tr><td>{label}</td><td>{old} → {d['geometric_candidates']}</td><td>{d['unknown_category']}</td><td>{d['folded_paths']}</td><td>{d['legend_associations']}</td><td><a href='{name}/geometry/geometry.png'>连线/图例叠加图</a> · <a href='{name}/geometry/geometry.json'>候选依据</a></td></tr>")
    manifest=json.loads((out/'sample-d-final/architecture-run.json').read_text())
    body='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>工程上下文与连线改造回归</title><style>body{font:16px/1.65 system-ui;max-width:1100px;margin:36px auto;padding:0 20px;color:#20323e}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ccc;text-align:left}aside{padding:16px;background:#fff2d8}a{color:#12629c}</style>
<h1>工程上下文与连线改造：开发回归</h1>
<aside>这些文件已用于开发。本报告不是新的独立泛化评测。几何图元数不是业务数，仍包含双线表达和错误候选。业务表均需人工复核。</aside>
<h2>已落地的架构</h2><p>全文分类 → 说明优先 → 带来源的陈述 → 逐图读取 → 独立对象候选与图纸表达 → 身份判定/复查任务 → 有限次数定向复读。当前上下文主要按OCR行提取，不是完整段落语义理解；设备、端口和纤芯还没有专用完整抽取。</p>
<h2>四张业务图的几何回归</h2><table><tr><th>样例</th><th>几何候选</th><th>类别未确定</th><th>短折返路径</th><th>本页图例关联候选</th><th>证据</th></tr>'''
    body+=''.join(rows)+'''</table>
<h2>项目D：真实本地4B模型回归</h2><p>重跑第7页业务图，其余26页复用。第一次原型通道关键字段1/13，第二次2/13，再对同批原始回答修正纯站名端点处理后为4/13；原基线为0/13。最终端点匹配11/13，仍有双线重复、系统/速率缺项和业务标注错配，不能无人复核交付。</p>
<p>执行了3次定向复查。模型复查仍有符号误读和自相矛盾，0条被程序确认为事实；回答单独保存，没有回填业务表。</p>
<p><a href="sample-d-final/review.html">最新逐页图文与Excel入口</a> · <a href="sample-d-final/structured.xlsx">Excel</a> · <a href="sample-d-final/engineering/context.json">施工说明陈述</a> · <a href="sample-d-final/engineering/graph.json">对象与图纸表达</a> · <a href="sample-d-final/engineering/review-tasks.json">复查任务</a> · <a href="sample-d-final/engineering/review-results.json">复查原始结果</a></p>
<p><a href="sample-d-final/audit/channel-results.json">原协议逐通道核对</a> · <a href="sample-d-final/architecture-run.json">运行范围及代码指纹</a> · <a href="tests.log">54项程序测试</a></p>
<h2>必须保留的边界</h2><ul><li>说明可能识别错误，不能作为本图缺失字段的替代证据。</li><li>说明数量只触发核对任务，不创造确认对象。</li><li>本页图例唯一匹配仍是视觉候选；OCR误字不按常识补速率。</li><li>当前只支持明确短直角折返，任意分支/跳线/交叉连接点仍未完整实现。</li><li>三阶段、回次、保护套数等身份信息不足时保持未确定，不按名称相似自动合并。</li></ul></html>'''
    (out/'review.html').write_text(body);(out/'summary.json').write_text(json.dumps({'geometry':summaries,'model_regression':manifest},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
