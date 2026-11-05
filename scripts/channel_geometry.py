"""Raster line/box proposals for channel diagrams. No station names or sample labels."""
import json
import re
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageDraw
from full_pdf import save


def segments(mask, horizontal):
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    result=[]
    for c in contours:
        x,y,w,h=cv2.boundingRect(c)
        if horizontal and h<=6:result.append([x,y+h/2,x+w,y+h/2])
        elif not horizontal and w<=6:result.append([x+w/2,y,x+w/2,y+h])
    return result


def node_boxes(hs,vs,w,h):
    boxes=[]
    for i,a in enumerate(vs):
        for b in vs[i+1:]:
            left,right=sorted([a,b],key=lambda v:v[0]);x0,x1=left[0],right[0]
            if not .012*w<x1-x0<.23*w:continue
            if abs(left[1]-right[1])>5 or abs(left[3]-right[3])>5:continue
            y0,y1=(left[1]+right[1])/2,(left[3]+right[3])/2
            if y1-y0<.025*h:continue
            def closed(y):return any(abs(s[1]-y)<5 and s[0]<=x0+5 and s[2]>=x1-5 for s in hs)
            if closed(y0) and closed(y1):boxes.append([x0/w,y0/h,x1/w,y1/h])
    return boxes


def containing_node(point,boxes):
    x,y=point
    choices=[b for b in boxes if b[0]-.003<=x<=b[2]+.003 and b[1]+.002<y<b[3]-.002]
    return min(choices,key=lambda b:(b[2]-b[0])*(b[3]-b[1])) if choices else None


def header_for_box(box,lines):
    if box is None:return []
    x0,y0,x1,y1=box;cx=(x0+x1)/2;candidates=[]
    for r in lines:
        b=r['bbox'];tx=(b[0]+b[2])/2
        if re.search(r'线路保护|套保护|通道|专用光纤|至.+变',r['text']):continue
        if y0-.09<b[3]<=y0+.006 and x0-.09<tx<x1+.09 and re.search(r'变|站|调|汇聚|中心|网',r['text']):
            distance=max(0,y0-b[3])+abs(tx-cx)*.35
            candidates.append((distance,r))
        # Small equipment boxes often put their caption on the left, not above.
        if b[2]<=x0+.006 and x0-b[2]<.06 and y0<(b[1]+b[3])/2<y1 and re.search(r'变|站|调|汇聚|中心',r['text']):
            distance=abs((b[1]+b[3])/2-(y0+y1)/2)+max(0,x0-b[2])
            candidates.append((distance,r))
    plain=plain_header(box,lines)
    if plain:
        nearest=plain[0]
        distance=max(0,y0-nearest['bbox'][3])+abs((nearest['bbox'][0]+nearest['bbox'][2])/2-cx)*.35
        candidates.append((distance,nearest))
    if not candidates:return []
    candidates.sort(key=lambda x:x[0]);best=candidates[0][1]
    # A network name may be wrapped onto a second line immediately below the station.
    selected=[best]
    for _,r in candidates[1:]:
        if abs((r['bbox'][0]+r['bbox'][2])/2-(best['bbox'][0]+best['bbox'][2])/2)<.03 and abs(r['bbox'][1]-best['bbox'][1])<.028:
            selected.append(r)
    return sorted(selected,key=lambda r:r['bbox'][1])


def join_collinear(segments,w,h):
    result=[]
    for s in sorted(segments,key=lambda s:(s[1],s[0])):
        if s[2]-s[0]<w*.075:continue
        match=next((r for r in result if abs(r[1]-s[1])<2.5 and -2<=s[0]-r[2]<w*.013),None)
        if match is not None:match[2]=max(match[2],s[2])
        else:result.append(s.copy())
    return result


def trace_channels(assets,destination):
    from drawing_topology import detect_ink,legend_entries,line_paths,symbol_at,match_legend,associate_symbols
    dest=Path(destination);dest.mkdir(parents=True,exist_ok=True)
    original=Image.open(Path(assets['overview']).parents[1]/'assets'/Path(assets['overview']).stem/'ocr.png').convert('RGB')
    image=original.copy();image.thumbnail((3072,3072));w,h=image.size
    gray=cv2.cvtColor(np.array(image),cv2.COLOR_RGB2GRAY);ink=detect_ink(gray)
    horizontal=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((1,max(16,int(w*.008))),np.uint8))
    vertical=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((max(8,int(h*.004)),1),np.uint8))
    hs=segments(horizontal,True);vs=segments(vertical,False)
    box_vertical=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((max(30,int(h*.022)),1),np.uint8))
    boxes=node_boxes(hs,segments(box_vertical,False),w,h)
    legends=legend_entries(assets['lines'],hs,boxes,gray)
    paths,unbound=line_paths(join_collinear(hs,w,h),vs,boxes,w,h)
    channels=[];pending=[]
    for path in paths:
        (sx,sy),(ex,ey)=path['points'][0],path['points'][-1]
        if any(e['bbox'][0]-.005<=sx<=e['bbox'][2] and e['bbox'][1]<=sy<=e['bbox'][3] for e in legends):continue
        left=containing_node((sx,sy),boxes);right=containing_node((ex,ey),boxes)
        if left is None or right is None or left==right:
            unbound.append({'line':[sx,sy,ex,ey],'path':path['points'],'reason':'endpoint boxes unresolved'});continue
        lh=header_for_box(left,assets['lines']);rh=header_for_box(right,assets['lines'])
        # Plain station names (e.g. two Chinese characters) are valid captions.
        # Keep them as visual candidates; context must not invent the spelling.
        if not lh:lh=plain_header(left,assets['lines'])
        if not rh:rh=plain_header(right,assets['lines'])
        if not lh or not rh:
            unbound.append({'line':[sx,sy,ex,ey],'path':path['points'],'reason':'endpoint header unresolved'});continue
        pending.append((path,left,right,lh,rh))
    for path,left,right,lh,rh in pending:
        (sx,sy),(ex,ey)=path['points'][0],path['points'][-1]
        x0=min(p[0] for p in path['points']);x1=max(p[0] for p in path['points'])
        ys=[p[1] for p in path['points']];y=min(ys)
        # Assign labels by nearest competing path, including labels beyond a node.
        row=[]
        for r in assets['lines']:
            b=r['bbox'];cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2
            if not x0-.20<cx<x1+.22:continue
            distance=min(abs(cy-py) for py in ys)
            if distance>.024:continue
            rivals=[min(abs(cy-p[1]) for p in other[0]['points']) for other in pending
                    if other[0] is not path and min(x1,max(p[0] for p in other[0]['points']))>max(x0,min(p[0] for p in other[0]['points']))]
            if rivals and min(rivals)+.001<distance:continue
            row.append(r)
        header_ids={r['id'] for r in lh+rh};row=[r for r in row if r['id'] not in header_ids and not any(r['id']==e['source_id'] for e in legends)]
        symbols=[symbol_at(gray,sx,sy),symbol_at(gray,ex,ey)]
        symbol=symbols[0] if symbols[0]==symbols[1] else 'unknown'
        evidence=associate_symbols(gray,[(sx,sy),(ex,ey)],legends)
        local_categories={e['category'] for e in evidence if e['category']}
        explicit_protection=any(re.search(r'线路保护|套保护|保护装置',r['text']) for r in row)
        explicit_other=any(re.search(r'调度|IAD|[IT]P电话|测距|行政',r['text'],re.I) for r in row)
        category='保护业务' if explicit_protection else ('非保护业务' if explicit_other else next(iter(local_categories)) if len(local_categories)==1 else None)
        category_conflict=bool((explicit_protection and '非保护业务' in local_categories) or (explicit_other and not explicit_protection and '保护业务' in local_categories))
        if category_conflict:category=None
        # Crossing a box provides an intermediate candidate, never a terminal.
        middle=[r for b in boxes if b not in [left,right] and x0+.01<(b[0]+b[2])/2<x1-.01 and b[1]<y<b[3] for r in header_for_box(b,assets['lines']) or plain_header(b,assets['lines'])]
        legend_rows=[r for r in assets['lines'] if any(r['id']==e['source_id'] for e in evidence)]
        sources={r['id']:r for r in lh+rh+row+middle+legend_rows}
        # Legend is supplied separately; do not make the line crop cover the entire page.
        crop_sources=lh+rh+row+middle
        bounds=[max(0,min(x0-.02,min(r['bbox'][0] for r in crop_sources))-.01),
                max(0,min(min(ys)-.025,min(r['bbox'][1] for r in crop_sources))-.01),
                min(1,max(x1+.02,max(r['bbox'][2] for r in crop_sources))+.01),min(1,max(max(ys)+.03,max(r['bbox'][3] for r in crop_sources)))]
        cid=f'C{len(channels)+1:03d}';ow,oh=original.size
        crop=original.crop(tuple(round(v*s) for v,s in zip(bounds,(ow,oh,ow,oh))));crop.thumbnail((3072,3072));file=dest/f'{cid}.png';crop.save(file)
        strip_bounds=[bounds[0],max(0,min(ys)-.025),bounds[2],min(1,max(ys)+.025)]
        strip=original.crop(tuple(round(v*s) for v,s in zip(strip_bounds,(ow,oh,ow,oh))));strip.thumbnail((3072,768));stripfile=dest/f'{cid}-row.png';strip.save(stripfile)
        channels.append({'id':cid,'line':[sx,sy,ex,ey],'path':path['points'],'path_kind':path['kind'],
                         'a_box':left,'z_box':right,'a_headers':[r['id'] for r in lh],'z_headers':[r['id'] for r in rh],
                         'intermediate_headers':[r['id'] for r in middle],'row_ids':[r['id'] for r in row],
                         'category':category,'category_conflict':category_conflict,'symbol':symbol,'legend_evidence':evidence,
                         'bbox':bounds,'image':str(file),'row_image':str(stripfile),'lines':list(sources.values()),'status':'geometric_candidate'})
    # Double strokes are not silently merged. Pair hypotheses require a local
    # double-stroke legend and are sent to review until their identity is proven.
    pairs=[]
    for i,a in enumerate(channels):
        for b in channels[i+1:]:
            if a['a_box']==b['a_box'] and a['z_box']==b['z_box'] and .002<abs(a['line'][1]-b['line'][1])<.012:
                evidence=match_legend('double',legends)
                if evidence:pairs.append({'members':[a['id'],b['id']],'interpretation':'possible single fiber-resource expression','legend_evidence':evidence,'decision':'证据不足，不自动合并'})
    debug=image.copy();draw=ImageDraw.Draw(debug)
    for b in boxes:draw.rectangle(tuple(v*s for v,s in zip(b,(w,h,w,h))),outline='blue',width=2)
    for c in channels:
        points=[(x*w,y*h) for x,y in c['path']];draw.line(points,fill='red',width=3);draw.text(points[0],c['id'],fill='red')
    for e in legends:draw.rectangle(tuple(v*s for v,s in zip(e['bbox'],(w,h,w,h))),outline='green',width=2)
    debug.save(dest/'geometry.png')
    result={'method':'contrast_preserving_orthogonal_paths_scoped_legend_v2','channels':channels,'unbound_lines':unbound,
            'boxes':boxes,'legend_entries':legends,'parallel_expression_hypotheses':pairs,
            'scope':'geometric hypotheses; unknown symbols/categories and ambiguous crossings are not confirmed business relationships'}
    save(dest/'geometry.json',result);return result


def plain_header(box,lines):
    x0,y0,x1,y1=box;cx=(x0+x1)/2
    candidates=[r for r in lines if re.fullmatch(r'[\u4e00-\u9fff]{2,8}',r['text'].strip())
                and y0-.05<r['bbox'][3]<y0+.004 and abs((r['bbox'][0]+r['bbox'][2])/2-cx)<.025
                and not re.search(r'保护|通道|图例|说明|设备|光纤|光缆|省干|传输网',r['text'])]
    return sorted(candidates,key=lambda r:y0-r['bbox'][3])[:1]
