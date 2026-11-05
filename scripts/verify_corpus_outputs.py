"""Check final artifact integrity without treating JSON validity as accuracy."""
import json,re,sys
from datetime import datetime,timezone
from pathlib import Path
from openpyxl import load_workbook
from full_pdf import sha256,save
from validate_corpus import source_path

root=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]);manifest=json.loads((out/'validation.json').read_text());checks=[]
assert all(sha256(out/'frozen-source'/name)==digest for name,digest in manifest['code'].items()),'Frozen code snapshot changed'
changed={name:{'frozen':digest,'current':sha256(source_path(name))} for name,digest in manifest['code'].items() if sha256(source_path(name))!=digest}
if changed:
    assert len(sys.argv)>2,'Current source changed; supply the post-validation repair manifest'
    repair=json.loads(Path(sys.argv[2]).read_text())
    assert Path(repair['source_validation'])==out.resolve()
    assert changed==repair['code_changes'],'Unrecorded source changes after validation'
for d in manifest['documents']:
    folder=Path(d['output']);run=json.loads((folder/'run.json').read_text())
    pages=list((folder/'pages').glob('*.json'))
    assert len(pages)==d['expected_pages']==run['page_count']
    assert sha256(Path(d['input']))==d['pdf_sha256']==run['signature']['pdf_sha256']
    observations=json.loads((folder/'observations.json').read_text())
    assert len({r['id'] for r in observations})==len(observations)
    for row in observations:
        for e in row.get('field_evidence',{}).values():
            assert e['sources']
            for source in e['sources']:
                assert len(source['bbox'])==4 and all(0<=v<=1 for v in source['bbox'])
    book=load_workbook(folder/'structured.xlsx',read_only=True)
    assert all(sheet in book.sheetnames for sheet in ['项目','光缆','光路','保护业务','非保护业务'])
    formulas=[(sheet.title,cell.coordinate) for sheet in book for row in sheet for cell in row if cell.data_type=='f']
    assert not formulas,formulas
    book.close()
    broken=[]
    for href in re.findall(r"(?:href|src)=['\"]([^'\"]+)['\"]",(folder/'review.html').read_text()):
        if href.startswith(('#','http:','https:','data:')):continue
        if not (folder/href.split('#')[0]).exists():broken.append(href)
    assert not broken,broken
    protocol=json.loads((folder/'audit/protocol.json').read_text())
    assert protocol['is_model_input'] is False
    checks.append({'name':d['name'],'pages':len(pages),'source_hash_unchanged':True,
                   'unique_observation_ids':True,'evidence_boxes_valid':True,'excel_formula_cells':0,'broken_review_links':0})
result={'verified_utc':datetime.now(timezone.utc).isoformat(),'frozen_code_snapshot_matches':True,
        'current_code_changes_after_validation':changed,'documents':checks,'not_an_accuracy_check':True}
save(out/'artifact-verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
