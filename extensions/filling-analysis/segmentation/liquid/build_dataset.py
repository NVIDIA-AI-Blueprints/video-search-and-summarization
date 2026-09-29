# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LOCAL WEAK LABEL generation. Never imported by learned inference.

Uses HSV surface cues and a reviewed bottle silhouette to bootstrap training.
These are weak labels, not independent segmentation ground truth.
"""
from pathlib import Path
import json, hashlib, sys, os
import cv2
import numpy as np

EXT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(EXT))
from backend.cycle_vision import analyze_video, measure_frame, load_calibration
ROOT=Path(os.environ['FILLING_TRAINING_DIR']).expanduser().resolve()
OUT=ROOT/'liquid-dataset'
VIDEO=EXT/'media/orange_juice_5min_balanced_vios_20260922.mp4'
# Reviewed silhouette in 640x360 frame, translated by observed collar center.
SILHOUETTE=np.array([[309,82],[333,82],[338,107],[342,144],[345,175],[372,193],
 [377,206],[377,298],[371,316],[359,323],[283,323],[272,314],[268,294],
 [268,199],[274,177],[284,151],[299,114],[305,96]],np.int32)
SPLITS={'train':[1,2,5,8], 'valid':[6,7], 'test':[10,13,15,16]}

def main():
 OUT.mkdir(exist_ok=True)
 result=analyze_video(str(VIDEO))
 (ROOT/'liquid-artifacts/cycle_selection.json').write_text(json.dumps(result['cycles'],indent=2))
 cal=load_calibration(); cap=cv2.VideoCapture(str(VIDEO)); records=[]; selected_panels=[]
 for split, cycle_numbers in SPLITS.items():
  folder=OUT/split; folder.mkdir(exist_ok=True)
  coco={'info':{'description':'Weak HSV+reviewed-silhouette labels; not independent ground truth'},
        'licenses':[], 'images':[], 'annotations':[],
        'categories':[{'id':1,'name':'visible_liquid','supercategory':'liquid'}]}
  for cn in cycle_numbers:
   cycle=result['cycles'][cn-1]
   times=np.arange(cycle['start_time'],cycle['end_time']+.001,.25 if split=='train' else .5)
   for t in times:
    cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,frame=cap.read()
    if not ok: raise RuntimeError('frame missing')
    small=cv2.resize(frame,(640,360)); f=measure_frame(small,cal)
    if not f['aligned'] or f['level'] is None: continue
    dx=round(f['center']-320); shape=SILHOUETTE.copy();shape[:,0]+=dx
    bottle=np.zeros((360,640),np.uint8);cv2.fillPoly(bottle,[shape],255)
    boundary=round(125+(1-f['level'])*190)
    # Upper-limit samples require visible orange in the neck to extend mask.
    # This cue is weak annotation only. Inference calls the trained RF-DETR.
    if f['level']>=.975:
     hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
     orange=cv2.inRange(hsv,(5,115,115),(36,255,255))>0
     rows=np.arange(82,126)
     fractions=np.array([np.mean(orange[y][bottle[y]>0]) if np.any(bottle[y]>0) else 0 for y in rows])
     valid=np.flatnonzero(fractions>.65)
     if len(valid): boundary=int(rows[valid[0]])
    hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
    color=cv2.inRange(hsv,(5,90,90),(36,255,255))
    weak=cv2.bitwise_and(bottle,color);weak[:boundary]=0
    weak=cv2.morphologyEx(weak,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    weak=cv2.bitwise_and(weak,bottle)
    cs,_=cv2.findContours(weak,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    cleaned=np.zeros_like(weak)
    for contour in cs:
     if cv2.contourArea(contour)>60:cv2.drawContours(cleaned,[contour],-1,255,-1)
    weak=cleaned
    if f['level']<.02:weak[:]=0
    # Crop full bottle, not a central strip; jitter is only a training-view augmentation.
    x0,x1=250+dx,395+dx;y0,y1=70,335
    image=small[y0:y1,x0:x1];mask=weak[y0:y1,x0:x1]
    image_id=len(coco['images'])+1;name=f'cycle{cn:02d}_{round(t*1000):06d}.png'
    cv2.imwrite(str(folder/name),image)
    maskname=name.replace('.png','_mask.png');cv2.imwrite(str(folder/maskname),mask)
    h,w=image.shape[:2]
    coco['images'].append({'id':image_id,'file_name':name,'height':h,'width':w})
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    polygons=[]
    for c in contours:
     if cv2.contourArea(c)<20:continue
     c=cv2.approxPolyDP(c,1.0,True).reshape(-1,2)
     if len(c)>=3:polygons.append(c.astype(float).reshape(-1).tolist())
    if polygons:
     ys,xs=np.where(mask>0)
     coco['annotations'].append({'id':image_id,'image_id':image_id,'category_id':1,
       'segmentation':polygons,'area':int(np.sum(mask>0)),
       'bbox':[int(xs.min()),int(ys.min()),int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1)],'iscrowd':0})
    records.append({'file':f'{split}/{name}','mask':f'{split}/{maskname}','split':split,'source_time':float(t),
      'cycle':cn,'surface_weak_visible_level':f['level'],'image_sha256':hashlib.sha256(image.tobytes()).hexdigest(),
      'label_provenance':'HSV surface fit + reviewed full-bottle silhouette, weak supervision'})
    if abs(t-cycle['start_time'])<.01 or abs(t-(cycle['fill_end_time'] or cycle['end_time']))<.15 or abs(t-cycle['end_time'])<.15:
     overlay=image.copy();overlay[mask>0]=(overlay[mask>0]*.5+np.array([30,240,255])*.5).astype(np.uint8)
     panel=np.zeros((300,290,3),np.uint8);panel[35:,:145]=image;panel[35:,145:]=overlay
     cv2.putText(panel,f'{split} c{cn} {t:.2f}s',(4,20),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
     selected_panels.append(panel)
  (folder/'_annotations.coco.json').write_text(json.dumps(coco))
 cap.release()
 # Content-hash audit catches exact duplicates; repeated synthetic compositions can
 # still be near-duplicates and must not be sold as independent generalization.
 cross=[]
 for a in records:
  for b in records:
   if a['split']=='train' and b['split']!='train' and a['image_sha256']==b['image_sha256']:
    cross.append([a['file'],b['file']])
 manifest={'source_file':str(VIDEO),'source_sha256':hashlib.sha256(VIDEO.read_bytes()).hexdigest(),
  'annotation_kind':'weak HSV + reviewed silhouette; no independently annotated ground truth',
  'split_unit':'entire bottle cycle','split_cycles':SPLITS,'records':records,
  'exact_cross_split_duplicates':cross,
  'limitation':'Repeated edited/synthetic compositions may share content despite cycle-disjoint split; metrics measure weak-label agreement, not production accuracy.'}
 (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
 for page in range(0,len(selected_panels),12):
  panels=selected_panels[page:page+12];panels += [np.zeros_like(panels[0])]*(12-len(panels))
  grid=np.vstack([np.hstack(panels[i:i+4]) for i in range(0,12,4)])
  cv2.imwrite(str(ROOT/f'liquid-artifacts/weak-label-review-{page//12}.jpg'),grid)
 print(json.dumps({'samples':len(records),'counts':{s:sum(r['split']==s for r in records) for s in SPLITS},'exact_cross_split_duplicates':len(cross)},indent=2))

if __name__=='__main__':main()
