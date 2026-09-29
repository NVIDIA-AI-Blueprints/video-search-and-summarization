# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Visible-height measurements derived exclusively from RF-DETR instance masks.

No image pixels, HSV calibration, expected liquid levels or cycle labels enter
this helper. A missing model mask remains unknown, never an inferred empty fill.
"""
from __future__ import annotations
import cv2
import numpy as np

VERSION = '2.0.0'
ALGORITHM = 'rfdetr-mask-cycle-v2'


def measure_masks(bottle_mask: np.ndarray, liquid_mask: np.ndarray,
                  bottle_confidence: float, liquid_confidence: float) -> dict:
    bottle=np.asarray(bottle_mask,dtype=bool)
    liquid=np.asarray(liquid_mask,dtype=bool)
    if bottle.ndim!=2 or liquid.shape!=bottle.shape:
        raise ValueError('Bottle and liquid masks must be matching full-frame HxW arrays')
    height,width=bottle.shape
    result={'level':None,'confidence':0.,'surface':[],'measurement_box':[],
            'measurement_state':'missing_bottle_mask','level_kind':'detected-bottle-visible-height',
            'measurement_engine':'rfdetr','geometry':None}
    by,bx=np.where(bottle)
    if len(bx)<100:return result
    x0,x1=int(bx.min()),int(bx.max())+1;y0,y1=int(by.min()),int(by.max())+1
    bw,bh=x1-x0,y1-y0
    result['measurement_box']=[x0/width,y0/height,bw/width,bh/height]
    result['geometry']={'box_pixels':[x0,y0,bw,bh],'center_x':(x0+x1)/(2*width),
                        'center_y':(y0+y1)/(2*height),'bottle_height_pixels':bh,
                        'bottle_width_pixels':bw}
    if min(bw,bh)<20 or x0<=0 or x1>=width or y0<=0 or y1>=height:
        result['measurement_state']='partial_or_tiny_bottle';return result
    if not np.isfinite(bottle_confidence) or bottle_confidence<.4:
        result['measurement_state']='low_bottle_confidence';return result
    liquid=liquid&bottle
    if not liquid.any() or not np.isfinite(liquid_confidence) or liquid_confidence<.4:
        result['measurement_state']='missing_liquid_mask';return result
    # Robust upper liquid boundary across central columns. Using many columns
    # prevents one thin model fragment or a jet from setting the surface height.
    left=int(round(x0+bw*.2));right=int(round(x0+bw*.8))
    tops=[]
    for x in range(left,right):
        ys=np.flatnonzero(liquid[:,x])
        if len(ys)>=max(3,int(bh*.025)):
            tops.append(float(ys[0]))
    if len(tops)<max(5,(right-left)*.35):
        result['measurement_state']='insufficient_liquid_boundary';return result
    boundary=float(np.median(tops));mad=float(np.median(np.abs(np.asarray(tops)-boundary)))
    if mad/bh>.08:
        result['measurement_state']='irregular_liquid_boundary';return result
    # The denominator is the actually detected bottle's neck-to-base visible
    # span, not an old calibrated strip and not a conversion to bottle volume.
    level=float(np.clip((y1-1-boundary)/(bh-1),0.,1.))
    confidence=float(min(bottle_confidence,liquid_confidence)*max(0.,1.-mad/bh*3))
    row=int(round(boundary)); xs=np.flatnonzero(bottle[row])
    if not len(xs):
        result['measurement_state']='surface_outside_bottle';return result
    result.update(level=round(level,5),confidence=round(confidence,5),
        surface=[[float(xs[0])/width,boundary/height],[float(xs[-1])/width,boundary/height]],
        measurement_state='measured_rf_mask_boundary',
        boundary_mad_pixels=round(mad,3),boundary_y=round(boundary,3),
        liquid_area_fraction=round(float(liquid.sum()/bottle.sum()),6))
    return result


def masks_from_polygons(instance: dict, width: int, height: int) -> tuple[np.ndarray,np.ndarray]:
    """Reconstruct a cached model result; ring parity retains predicted holes."""
    masks=[]
    for key in ('bottle_mask','liquid_mask'):
        mask=np.zeros((height,width),np.uint8);rings=[]
        for ring in instance.get(key,[]):
            if len(ring)<3:continue
            p=np.asarray(ring,dtype=float)
            if p.ndim!=2 or p.shape[1]!=2 or not np.isfinite(p).all():
                raise ValueError('Invalid model-mask polygon')
            coords=np.rint(p*np.array([width,height])).astype(np.int32)
            coords[:,0]=np.clip(coords[:,0],0,width-1);coords[:,1]=np.clip(coords[:,1],0,height-1)
            rings.append(coords)
        if rings:cv2.fillPoly(mask,rings,1)
        masks.append(mask.astype(bool))
    return masks[0],masks[1]


def measure_instance(instance: dict,width: int,height: int) -> dict:
    if isinstance(instance.get('measurement'),dict) and instance['measurement'].get('measurement_engine')=='rfdetr':
        return dict(instance['measurement'])
    bottle,liquid=masks_from_polygons(instance,width,height)
    return measure_masks(bottle,liquid,instance.get('bottle_confidence',0.),instance.get('liquid_confidence',0.))
