"""Compare two audited runs of the same PDF with the exact same check definitions."""
import json
import sys
from pathlib import Path
from full_pdf import save, sha256

base,new=map(Path,sys.argv[1:3])
b=json.loads((base/'run.json').read_text());n=json.loads((new/'run.json').read_text())
assert b['signature']['pdf_sha256']==n['signature']['pdf_sha256'], 'Different PDFs'
assert sha256(base/'audit/protocol.json')==sha256(new/'audit/protocol.json'), 'Different checks'
before=json.loads((base/'audit/results.json').read_text());after=json.loads((new/'audit/results.json').read_text())
indexed={c['id']:c for c in before['checks']}
comparison={'baseline':str(base.resolve()),'current':str(new.resolve()),
            'scope':'同一PDF、同一20项局部检查；不是全文件准确率。核心字段提取范围及整理策略已改变。',
            'baseline_matches':before['final_complete_matches'],'current_matches':after['final_complete_matches'],
            'check_count':after['check_count'],'rows':[]}
for c in after['checks']:
    old=indexed[c['id']]
    comparison['rows'].append({'id':c['id'],'description':c['description'],
                               'before_ids':old['final_matching_ids'],'after_ids':c['final_matching_ids'],
                               'change':int(bool(c['final_matching_ids']))-int(bool(old['final_matching_ids']))})
save(new/'audit/comparison.json',comparison)
print(json.dumps({k:v for k,v in comparison.items() if k!='rows'},ensure_ascii=False,indent=2))
