# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RF-DETR learned liquid masks. This module contains no HSV fallback."""
from pathlib import Path
import hashlib
import json
import time
import cv2
import numpy as np
from PIL import Image
from rfdetr import RFDETRSegNano

class LiquidSegmenter:
    def __init__(self, checkpoint_path: str, device: str = 'cuda:0', threshold: float = .4):
        path=Path(checkpoint_path)
        if not path.is_file():
            raise FileNotFoundError('A local trained liquid checkpoint is required; runtime never downloads one')
        self.checkpoint_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        self.threshold=threshold
        self.model=RFDETRSegNano(pretrain_weights=str(path),device=device,resolution=312,num_classes=1)
        self.identity={'architecture':'RF-DETR-Seg Nano','rfdetr_version':'1.10.1',
            'checkpoint_sha256':self.checkpoint_sha256,'class':'visible_liquid',
            'training_provenance':'Source-adapted weak HSV+reviewed-silhouette annotations; not independent mask ground truth',
            'inference':'Neural model mask; no calibrated-HSV fallback',
            'scope':'Five-minute fixed-camera bottle crops; other views are out of validated domain'}

    def predict(self, crop_rgb: np.ndarray) -> dict:
        if crop_rgb.ndim!=3 or crop_rgb.shape[2]!=3 or crop_rgb.dtype!=np.uint8:
            raise ValueError('crop_rgb must be uint8 RGB HxWx3')
        h,w=crop_rgb.shape[:2]
        if h<8 or w<8:raise ValueError('Bottle crop is too small')
        start=time.perf_counter()
        detections=self.model.predict(Image.fromarray(crop_rgb),threshold=self.threshold)
        mask=np.zeros((h,w),dtype=bool);confidence=0.
        if len(detections) and detections.mask is not None:
            index=int(np.argmax(detections.confidence))
            mask=np.asarray(detections.mask[index],dtype=bool)
            confidence=float(detections.confidence[index])
            if mask.shape!=(h,w):
                mask=cv2.resize(mask.astype(np.uint8),(w,h),interpolation=cv2.INTER_NEAREST)>0
        contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        polygons=[]
        for contour in contours:
            if cv2.contourArea(contour)<4:continue
            points=cv2.approxPolyDP(contour,1.,True).reshape(-1,2)
            if len(points)>=3:polygons.append([[float(x)/w,float(y)/h] for x,y in points])
        return {'mask':mask,'raw_mask':mask,'confidence':confidence,'polygons':polygons,
                'model':self.identity,'inference_ms':round((time.perf_counter()-start)*1000,3),
                'status':'mask_predicted' if mask.any() else 'no_liquid_mask_above_threshold'}
