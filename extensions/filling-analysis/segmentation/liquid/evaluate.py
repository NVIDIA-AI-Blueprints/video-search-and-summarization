# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Held-out-cycle weak-label comparison and real RF-DETR crop visual checks."""
from pathlib import Path
import sys,json,hashlib,time,os
import cv2,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from inference import LiquidSegmenter
ROOT=Path(os.environ['FILLING_TRAINING_DIR']).expanduser().resolve()

def overlay(image,mask):
 result=image.copy();result[mask]=(result[mask]*.55+np.array([40,220,255])*.45).astype(np.uint8)
 return result

def main():
 checkpoint=Path(sys.argv[1])
 engine=LiquidSegmenter(str(checkpoint),device='cuda:0')
 manifest=json.loads((ROOT/'liquid-dataset/manifest.json').read_text())
 details=[];panels=[]
 for record in manifest['records']:
  if record['split']=='train':continue
  bgr=cv2.imread(str(ROOT/'liquid-dataset'/record['file']))
  target=cv2.imread(str(ROOT/'liquid-dataset'/record['mask']),0)>0
  pred=engine.predict(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB));mask=pred['mask']
  inter=int(np.sum(mask&target));union=int(np.sum(mask|target))
  item={k:record[k] for k in ['file','split','cycle','source_time']}
  item.update(weak_label_iou=inter/union if union else 1.,target_pixels=int(target.sum()),
     predicted_pixels=int(mask.sum()),confidence=pred['confidence'],inference_ms=pred['inference_ms'])
  details.append(item)
  if record['cycle'] in [6,10,13,15,16] and (record['source_time']%1==0 or item['target_pixels']==0):
   h,w=bgr.shape[:2];panel=np.zeros((h+40,w*3,3),np.uint8)
   panel[40:,:w]=bgr;panel[40:,w:w*2]=overlay(bgr,target);panel[40:,w*2:]=overlay(bgr,mask)
   cv2.putText(panel,f"c{record['cycle']} {record['source_time']:.2f}s weak-IoU {item['weak_label_iou']:.3f}",(3,17),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1)
   cv2.putText(panel,'frame | weak label | RF-DETR',(3,34),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1)
   panels.append(panel)
 stats={}
 for split in ['valid','test']:
  subset=[r for r in details if r['split']==split]
  positives=[r for r in subset if r['target_pixels']>0];negatives=[r for r in subset if r['target_pixels']==0]
  stats[split]={'count':len(subset),'mean_weak_label_iou':float(np.mean([r['weak_label_iou'] for r in subset])),
     'mean_positive_weak_label_iou':float(np.mean([r['weak_label_iou'] for r in positives])),
     'empty_weak_label_samples':len(negatives),'empty_samples_with_mask':sum(r['predicted_pixels']>0 for r in negatives)}
 for page in range(0,len(panels),8):
  ps=panels[page:page+8];ps += [np.zeros_like(ps[0])]*(8-len(ps))
  grid=np.vstack([np.hstack(ps[i:i+2]) for i in range(0,8,2)])
  cv2.imwrite(str(ROOT/f'liquid-artifacts/heldout-comparison-{page//8}.jpg'),grid)
 # Full RF-DETR bottle-mask crops include unknown views and the hose-washing scene.
 crop_details=[];rfpanels=[]
 paths=ROOT/'bottle-artifacts/final'
 for name in ['sammy-0029.00','sammy-0107.25','sammy-0182.50','sammy-0085.00','sammy-0235.00','original-0095.00']:
  meta=json.loads((paths/(name+'.json')).read_text());frame=cv2.imread(str(paths/(name+'-frame.jpg')))
  npz=np.load(paths/(name+'-masks.npz')); masks=npz[npz.files[0]]
  h,w=frame.shape[:2];canvas=frame.copy()
  for i,bottle in enumerate(meta['bottles']):
   bx,by,bw,bh=bottle['box_pixels']
   pad_x=int(bw*.15);pad_y=int(bh*.05)
   x0=max(0,bx-pad_x);x1=min(w,bx+bw+pad_x);y0=max(0,by-pad_y);y1=min(h,by+bh+pad_y)
   rgb=cv2.cvtColor(frame[y0:y1,x0:x1],cv2.COLOR_BGR2RGB)
   pred=engine.predict(rgb);raw=pred['mask'];bmask=masks[i,y0:y1,x0:x1]
   clipped=raw&bmask
   canvas[y0:y1,x0:x1]=overlay(canvas[y0:y1,x0:x1],clipped)
   crop_details.append({'frame':name,'bottle':i+1,'box_pixels':[bx,by,bw,bh],
    'confidence':pred['confidence'],'mask_pixels':int(clipped.sum()),'bottle_pixels':int(bmask.sum()),
    'outside_bottle_rejected_pixels':int(np.sum(raw&~bmask)),
    'evaluation':'Visual-only; no independent mask ground truth for this RF crop'})
  target_w=640;canvas=cv2.resize(canvas,(target_w,round(h*target_w/w)))
  panel=np.zeros((canvas.shape[0]+30,target_w,3),np.uint8);panel[30:]=canvas
  cv2.putText(panel,name+' model mask clipped by bottle',(5,21),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
  rfpanels.append(panel)
  cv2.imwrite(str(ROOT/('liquid-artifacts/rf-crop-'+name+'.jpg')),panel)
 summary={'model':engine.identity,'threshold':engine.threshold,'split_stats':stats,
   'inference_median_ms':float(np.median([x['inference_ms'] for x in details][1:])),
   'details':details,'rf_crop_checks':crop_details,
   'scope':'Metrics are agreement with weak annotation, not independent ground truth. Whole cycles held out; repeated synthetic compositions still limit generalization.'}
 (ROOT/'liquid-artifacts/evaluation.json').write_text(json.dumps(summary,indent=2))
 print(json.dumps({k:v for k,v in summary.items() if k!='details'},indent=2))

if __name__=='__main__':main()
