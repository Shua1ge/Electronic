"""Find diagram groups from connected ink, without hand-selected regions."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from full_pdf import save


def detect_regions(assets, destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    source=Image.open(assets['overview']).convert('RGB');w,h=source.size
    gray=cv2.cvtColor(np.array(source),cv2.COLOR_RGB2GRAY)
    ink=(gray<180).astype(np.uint8)
    # Exclude the paper frame and the legend/title block, retaining the full source.
    legend=[l['bbox'][1] for l in assets['lines'] if l['text'].strip().startswith('图例')]
    cutoff=min(legend)-.01 if legend else .88
    ink[:int(h*.04)]=0;ink[int(h*cutoff):]=0
    ink[:,:int(w*.04)]=0;ink[:,int(w*.96):]=0
    connected=cv2.dilate(ink,np.ones((15,31),np.uint8))
    count,labels,stats,_=cv2.connectedComponentsWithStats(connected)
    components={i:stats[i] for i in range(1,count) if stats[i,cv2.CC_STAT_AREA]>w*h*.004
                and stats[i,cv2.CC_STAT_WIDTH]>w*.08 and stats[i,cv2.CC_STAT_HEIGHT]>h*.04}
    groups={i:[] for i in components}
    unassigned=[]
    for line in assets['lines']:
        x0,y0,x1,y1=[max(0,int(v*s)) for v,s in zip(line['bbox'],(w,h,w,h))]
        box=labels[y0:min(h,y1+1),x0:min(w,x1+1)]
        if not box.size:continue
        counts=np.bincount(box.ravel());counts[0]=0
        chosen=int(np.argmax(counts))
        if chosen in groups and counts[chosen]>box.size*.1:groups[chosen].append(line)
        elif line['bbox'][1]<cutoff:unassigned.append(line)
    # Tiny caption-only components are not diagrams; reconsider their lines as
    # captions instead of silently losing them when the component is discarded.
    for i in list(components):
        if len(groups[i])<3:
            unassigned.extend(groups.pop(i));components.pop(i)
    # Captions often sit just above a connected diagram; attach otherwise
    # unassigned text to the nearest group, never steal text assigned to another.
    for line in unassigned:
        b=line['bbox'];cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2;near=[]
        for i,(x,y,bw,bh,_) in components.items():
            dx=max(x/w-cx,0,cx-(x+bw)/w);dy=max(y/h-cy,0,cy-(y+bh)/h)
            if dx<.04 and dy<.055:near.append((dx*dx+dy*dy,i))
        if near:groups[min(near)[1]].append(line)
    high=Image.open(Path(assets['overview']).parents[1]/'assets'/Path(assets['overview']).stem/'ocr.png')
    hw,hh=high.size;regions=[]
    for i,stat in sorted(components.items(),key=lambda x:(x[1][1],x[1][0])):
        lines=groups[i]
        if len(lines)<3:continue
        x,y,bw,bh,_=stat
        b=[max(0,x/w-.01),max(0,y/h-.01),min(1,(x+bw)/w+.01),min(1,(y+bh)/h+.01)]
        b=[max(0,min(b[0],min(l['bbox'][0] for l in lines)-.01)),
           max(0,min(b[1],min(l['bbox'][1] for l in lines)-.01)),
           min(1,max(b[2],max(l['bbox'][2] for l in lines)+.01)),
           min(1,max(b[3],max(l['bbox'][3] for l in lines)+.01))]
        cropped=high.crop(tuple(round(v*s) for v,s in zip(b,(hw,hh,hw,hh))));cropped.thumbnail((3072,3072))
        path=destination/f'region-{len(regions)+1}.png';cropped.save(path)
        regions.append({'id':len(regions)+1,'bbox':b,'image':str(path),'lines':lines})
    save(destination/'regions.json',regions)
    return regions
