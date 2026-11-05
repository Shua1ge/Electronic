"""Normalize a dominant text orientation while preserving source coordinates."""
import json
import subprocess
from collections import Counter
from pathlib import Path
from PIL import Image

from document_core import ROOT
from full_pdf import save


def rotation_from_lines(lines):
    counts=Counter()
    for r in lines:
        if len(r['text'])>=4 and r.get('confidence',1)>=.2:
            angle=round(r.get('baseline_degrees',0)/90)*90 % 360
            counts[angle]+=len(r['text'])
    if not counts:return 0
    angle,weight=counts.most_common(1)[0]
    return (-angle)%360 if weight/sum(counts.values())>=.7 else 0


def rotate_box(box, angle):
    x0,y0,x1,y1=box
    return {0:[x0,y0,x1,y1],90:[y0,1-x1,y1,1-x0],
            180:[1-x1,1-y1,1-x0,1-y0],270:[1-y1,x0,1-y0,x1]}[angle]


def normalize_assets(assets, out, number):
    out=Path(out);folder=out/'assets'/f'{number:03d}'
    if assets.get('orientation_checked'):return assets,False
    lines=assets['lines'];high=Image.open(folder/'ocr.png');w,h=high.size
    if lines and not any('baseline_degrees' in r for r in lines):
        # Older runs lack oriented corners. Recheck only pages dominated by tall text.
        long=[r for r in lines if len(r['text'])>=4]
        tall=[r for r in long if (r['bbox'][3]-r['bbox'][1])*h > 2*(r['bbox'][2]-r['bbox'][0])*w]
        if len(tall)/max(1,len(long))>.6:
            probe=folder/'orientation-probe.json'
            subprocess.run([str(ROOT/'tools/bin/vision-ocr'),str(folder/'ocr.png'),str(probe)],check=True,timeout=180)
            measured=json.loads(probe.read_text());angle=rotation_from_lines(measured['lines'])
            assets['orientation_ocr_seconds']=measured['seconds']
        else:angle=0
    else:angle=rotation_from_lines(lines)
    assets['orientation_checked']=True;assets['rotation_ccw']=angle
    assets['original_size_px']=[w,h]
    if angle:
        original=folder/'unrotated-ocr.png'
        if not original.exists():high.save(original)
        high=high.rotate(angle,expand=True);high.save(folder/'ocr.png')
        overview=high.copy();overview.thumbnail((2048,2048));overview.save(assets['overview'])
        w,h=high.size
        for view in assets['detail_views']:
            tile=high.crop(tuple(round(v*s) for v,s in zip(view['bbox'],(w,h,w,h))))
            tile.thumbnail((2048,2048));tile.save(view['image'])
        for row in lines:
            row['original_bbox']=row['bbox'];row['bbox']=rotate_box(row['bbox'],angle)
        assets['coordinates']='normalized top-left xyxy after automatic rotation; original_bbox retained'
    save(folder/'ocr.json',assets)
    return assets,bool(angle)
