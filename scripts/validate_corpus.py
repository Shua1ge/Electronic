"""Freeze inference code and run the three known regression PDFs in full, sequentially."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from document_core import ROOT
from full_pdf import save,sha256

SOURCES=['evidence_pdf.py','evidence_core.py','document_core.py','full_pdf.py','run_document.py',
         'refine_routing.py','revalidate_evidence.py','routing_rules.py','page_orientation.py',
         'graphic_regions.py','drawing_topology.py','project_context.py','review_evidence.py','engineering_ontology.py','ontology_extraction.py','config/engineering-ontology.json','channel_geometry.py','channel_tasks.py','vision_ocr.swift','export_document.py']

def source_path(name):
    return ROOT/name if name.startswith('config/') else ROOT/'scripts'/name

def fingerprint():
    return {name:sha256(source_path(name)) for name in SOURCES}

def main():
    out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
    jobs=[('sample-b','sample-b*',28),('sample-c','sample-c*',24),('sample-d','sample-d*',27)]
    frozen=fingerprint();manifest=out/'validation.json'
    if manifest.exists():
        state=json.loads(manifest.read_text())
        if state['code']!=frozen:raise ValueError('Frozen validation code changed; choose a new validation directory')
    else:
        state={'started_utc':datetime.now(timezone.utc).isoformat(),'code':frozen,
               'model':json.loads((ROOT/'config/model.json').read_text()),'status':'running',
               'protocol':{'all_pages':True,'held_out_from_this_turn_development':False,'scope':'Known development PDFs; regression only, not independent generalization',
                 'manual_crops':False,'audit_labels_used_for_inference':False,
                 'semantic_audit':'Independently read original source drawings after code freeze. Audit all business-diagram channels and a fixed set of note/cable/optical facts. Report omissions, wrong endpoints, false positives and missing fields separately.',
                 'limits':'Three related telecom drawing sets; no claim of general document or real noisy-scan accuracy.'},'documents':[]}
        snapshot=out/'frozen-source';snapshot.mkdir(exist_ok=True)
        for name in SOURCES:
            (snapshot/name).parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source_path(name),snapshot/name)
        save(manifest,state)
    for name,pattern,count in jobs:
        if fingerprint()!=frozen:raise ValueError('Code changed during validation')
        pdfs=list((ROOT/'data/pdf_samples').glob(pattern+'.pdf'))
        if len(pdfs)!=1:raise ValueError(f'Expected one PDF for {pattern}')
        pdf=pdfs[0];target=out/name
        previous=next((d for d in state['documents'] if d['name']==name),None)
        if previous and previous.get('exit_code')==0:continue
        entry={'name':name,'input':str(pdf),'pdf_sha256':sha256(pdf),'expected_pages':count,'output':str(target),'status':'running'}
        state['documents']=[d for d in state['documents'] if d['name']!=name]+[entry];save(manifest,state)
        started=time.perf_counter();print(f'Starting ALL {count} pages: {pdf.name}',flush=True)
        with (out/f'{name}.log').open('a') as log:
            result=subprocess.run([sys.executable,str(ROOT/'scripts/run_document.py'),str(pdf),'--output',str(target)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        entry.update(exit_code=result.returncode,wall_seconds=time.perf_counter()-started,status='finished' if result.returncode==0 else 'failed')
        if (target/'run.json').exists():entry['run']=json.loads((target/'run.json').read_text())
        save(manifest,state);print(f"Finished {name}: exit={result.returncode}, {entry['wall_seconds']:.1f}s",flush=True)
    state['status']='finished' if all(d.get('exit_code')==0 for d in state['documents']) else 'partial'
    state['finished_utc']=datetime.now(timezone.utc).isoformat();save(manifest,state)

if __name__=='__main__':main()
