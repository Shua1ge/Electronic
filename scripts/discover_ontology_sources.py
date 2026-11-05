"""Read newly supplied local PDFs for ontology discovery, without model inference."""
import concurrent.futures
import hashlib
import json
import subprocess
from pathlib import Path
import pymupdf
from full_pdf import save,ROOT

OUT=ROOT/'outputs/ontology_discovery'
OLD={'sample-a.pdf','sample-b.pdf','sample-c.pdf','sample-d.pdf'}

def read_file(entry):
    p=ROOT/entry['file'];key=entry['sha256'][:16];folder=OUT/'sources'/key;folder.mkdir(parents=True,exist_ok=True)
    result=[];doc=pymupdf.open(p)
    for n,page in enumerate(doc,1):
        record=folder/f'{n:03d}.json'
        if record.exists():result.append(json.loads(record.read_text()));continue
        text=page.get_text();w,h=page.rect.width,page.rect.height
        image=folder/f'{n:03d}.png';page.get_pixmap(matrix=pymupdf.Matrix(3600/max(w,h),3600/max(w,h)),alpha=False).save(image)
        # Read all new pages through local OCR, including text PDFs; preserve
        # native text separately so mixed native/outlined content is not lost.
        ocr=folder/f'{n:03d}-ocr.json'
        subprocess.run([str(ROOT/'tools/bin/vision-ocr'),str(image),str(ocr)],check=True,timeout=180,stdout=subprocess.DEVNULL)
        raw=json.loads(ocr.read_text());lines=[{**l,'id':f'T{i:03d}'} for i,l in enumerate(raw['lines'],1)]
        r={'file':entry['file'],'document_sha256':entry['sha256'],'page':n,'group':entry['group'],
           'native_text':text,'lines':lines,'image':str(image),'discovery_only':True,'text':'\n'.join(l['text'] for l in lines)}
        save(record,r);result.append(r)
    doc.close();return result

def main():
    inventory=json.loads((OUT/'inventory.json').read_text());entries=[e for e in inventory if Path(e['file']).name not in OLD and 'error' not in e]
    # Identical bytes share one read; aliases are retained in the inventory.
    unique={e['sha256']:e for e in entries};pages=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs={pool.submit(read_file,e):e for e in unique.values()}
        for job in concurrent.futures.as_completed(jobs):
            r=job.result();pages.extend(r);print(f"{len(pages)} pages read: {jobs[job]['file']}",flush=True)
    save(OUT/'new-pages.json',sorted(pages,key=lambda r:(r['file'],r['page'])))
    print(json.dumps({'new_files':len(entries),'unique_files':len(unique),'pages_read':len(pages)},ensure_ascii=False))

if __name__=='__main__':main()
