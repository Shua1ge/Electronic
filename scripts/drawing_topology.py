"""Geometric proposals, scoped legend evidence and ambiguous-path retention.

No station dictionary or engineering audit labels. Symbol semantics are read from
this page's legend; unmatched symbols are explicitly unknown.
"""
import re
import cv2
import numpy as np


def detect_ink(gray):
    # Retain faint grey strokes; local contrast is saved as a diagnostic, not
    # interpreted as semantic confidence. Avoid filling the almost-white paper.
    return (gray < 245).astype(np.uint8)*255


def symbol_at(gray,x,y):
    h,w=gray.shape;r=max(7,round(w*.0045));px,py=round(x*w),round(y*h)
    patch=gray[max(0,py-r):min(h,py+r+1),max(0,px-r):min(w,px+r+1)]
    if patch.size==0:return 'unknown'
    ink=(patch<245).astype(np.uint8)*255
    contours,_=cv2.findContours(ink,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    # Hollow endpoint symbols have an inner contour unaffected by their line.
    candidates=[]
    for c in contours:
        area=cv2.contourArea(c); bx,by,bw,bh=cv2.boundingRect(c)
        if area<4 or bw<3 or bh<3 or bw>2*r-2 or bh>2*r-2:continue
        approx=cv2.approxPolyDP(c,.065*cv2.arcLength(c,True),True)
        fill=area/(bw*bh)
        if len(approx)==3:kind='triangle'
        elif len(approx)==4 and fill<.75:kind='diamond'
        elif fill>.55 and .6<bw/bh<1.6:kind='dot'
        else:continue
        candidates.append((area,kind))
    return max(candidates,default=(0,'unknown'))[1]


def legend_entries(lines,hs,boxes,gray):
    h,w=gray.shape;entries=[]
    for row in lines:
        b=row['bbox'];text=row['text']
        if not re.search(r'通道|光纤芯|纤芯|保护',text):continue
        if any(box[0]<b[0]<box[2] and box[1]<b[1]<box[3] for box in boxes):continue
        cy=(b[1]+b[3])/2
        # Legend samples are short strokes just left of their explanatory text,
        # outside equipment boxes; no bottom-of-page cutoff is applied to data.
        candidates=[s for s in hs if .018<(s[2]-s[0])/w<.095 and abs(s[1]/h-cy)<.012
                    and -.012<b[0]-s[2]/w<.04 and b[1]>.55]
        if not candidates:continue
        s=min(candidates,key=lambda s:abs(s[1]/h-cy));x0,y,x1,_=s;x0/=w;x1/=w;y/=h
        if any(box[0]<=x0<=box[2] and box[1]<=y<=box[3] for box in boxes):continue
        doubles=[t for t in candidates if t!=s and abs(t[1]-s[1])/h<.012 and abs(t[0]-s[0])/w<.006 and abs(t[2]-s[2])/w<.006]
        symbol='double' if doubles else 'endpoint_sample'
        rate=re.search(r'(?<![A-Za-z0-9.])(\d+(?:\.\d+)?[MG])',text)
        entries.append({'id':'L-'+row['id'],'source_id':row['id'],'text':text,'symbol':symbol,
                        'bbox':[x0,min(y-.015,b[1]),b[2],max(y+.015,b[3])],
                        'category':'保护业务' if '保护' in text else ('非保护业务' if re.search(r'调度|测距|电话|数据|以太网',text) else None),
                        'rate':rate[1] if rate else None,'patches':[endpoint_patch(gray,x0,y).tolist(),endpoint_patch(gray,x1,y).tolist()],'status':'本页图例候选'})
    return entries


def match_legend(symbol,entries):
    found=[e for e in entries if e['symbol']==symbol]
    meanings={(e['category'],e['rate'],e['text']) for e in found}
    return found if len(meanings)==1 else []


def line_paths(hs,vs,boxes,w,h):
    """Join only unambiguous short vertical U-turns, never arbitrary crossings."""
    def border(s):
        return any(abs(s[1]/h-b[y])<.003 and abs(s[0]/w-b[0])<.006 and abs(s[2]/w-b[2])<.006 for b in boxes for y in [1,3])
    horizontal=[s for s in hs if (s[2]-s[0])/w>.075 and (s[2]-s[0])/w<.9 and .025<s[1]/h<.975 and not border(s)]
    joins={};ambiguous=[]
    for i,a in enumerate(horizontal):
        for j,b in enumerate(horizontal[i+1:],i+1):
            if not .003<abs(a[1]-b[1])/h<.04:continue
            for end in [0,2]:
                if abs(a[end]-b[end])/w>.002:continue
                x=(a[end]+b[end])/2;y0,y1=sorted([a[1],b[1]])
                vertical=[v for v in vs if abs(v[0]-x)<3 and abs(v[1]-y0)<4 and abs(v[3]-y1)<4]
                if vertical and not any(abs(x/w-bx[k])<.004 and bx[1]<=y0/h<=bx[3] for bx in boxes for k in [0,2]):
                    joins.setdefault(i,[]).append((j,end));joins.setdefault(j,[]).append((i,end))
    used=set();paths=[]
    for i,a in enumerate(horizontal):
        if i in used:continue
        matches=joins.get(i,[])
        if len(matches)==1 and len(joins.get(matches[0][0],[]))==1:
            j,end=matches[0];b=horizontal[j];other=2-end
            paths.append({'points':[[a[other]/w,a[1]/h],[a[end]/w,a[1]/h],[b[end]/w,b[1]/h],[b[other]/w,b[1]/h]],'kind':'orthogonal_fold','segments':[a,b]});used.update([i,j])
        else:
            if matches:ambiguous.append({'line':[v/s for v,s in zip(a,(w,h,w,h))],'reason':'multiple possible turns; not joined'})
            paths.append({'points':[[a[0]/w,a[1]/h],[a[2]/w,a[1]/h]],'kind':'straight','segments':[a]});used.add(i)
    return paths,ambiguous


def endpoint_patch(gray,x,y):
    """Binary local symbol, with the through-line suppressed for comparison."""
    h,w=gray.shape;r=max(12,round(w*.006));px,py=round(x*w),round(y*h)
    padded=cv2.copyMakeBorder(gray,r,r,r,r,cv2.BORDER_CONSTANT,value=255)
    patch=(padded[py:py+2*r+1,px:px+2*r+1]<245).astype(np.uint8)
    vertical=cv2.morphologyEx(patch,cv2.MORPH_OPEN,np.ones((r+3,1),np.uint8))
    patch[vertical>0]=0
    patch[r-1:r+2,:]=0
    return patch


def symbol_similarity(a,b):
    a=np.asarray(a,dtype=np.uint8);b=np.asarray(b,dtype=np.uint8)
    if a.shape!=b.shape or a.sum()<4 or b.sum()<4:return 0.
    best=0.;r=a.shape[0]//2
    for mirrored in [b,np.fliplr(b)]:
        padded=np.pad(mirrored,6)
        for dx in range(-6,7):
            for dy in range(-2,3):
                crop=padded[6+dy:6+dy+a.shape[0],6+dx:6+dx+a.shape[1]]
                score=2*np.logical_and(a,crop).sum()/max(1,int(a.sum()+crop.sum()))
                best=max(best,float(score))
    return best


def associate_symbols(gray,endpoints,entries):
    if not entries:return []
    scores=[]
    for e in entries:
        if not e.get('patches') or e['symbol']=='double':continue
        score=min(max(symbol_similarity(endpoint_patch(gray,*point),template) for template in e['patches']) for point in endpoints)
        scores.append((score,e))
    scores.sort(key=lambda item:item[0],reverse=True)
    if not scores or scores[0][0]<.72:return []
    if len(scores)>1 and scores[0][0]-scores[1][0]<.08:return []
    return [{k:v for k,v in scores[0][1].items() if k!='patches'}|{'visual_match_score':scores[0][0]}]
