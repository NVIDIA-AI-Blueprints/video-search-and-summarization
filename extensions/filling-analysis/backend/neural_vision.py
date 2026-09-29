# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cycle decisions from RF-DETR masks, with explicitly separate spill evidence.

Bottle association, stationarity, liquid rise and final height come only from
model masks. The optional source-calibrated exterior color signal can establish
visible overflow, never fill height or completion. Event labels/counts are not
runtime inputs. The model was adapted with weak supervision to this recording.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from segmentation.measurements import ALGORITHM, VERSION, measure_instance

CALIBRATION_PATH = Path(__file__).with_name('neural_measurement_calibration.json')
OVERFLOW_ENGINE = 'calibrated-exterior-color-signal-v1'


def _sha(path: str | Path) -> str:
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def associate_tracks(observations: list[dict], max_gap: float=1.25) -> list[list[dict]]:
    """Associate a source's single visible bottle by detected center continuity."""
    tracks=[]; current=[]
    for o in observations:
        g=o['measurement'].get('geometry') if o.get('measurement') else None
        if not g:continue
        if current:
            previous=current[-1]; pg=previous['measurement']['geometry']
            gap=o['t']-previous['t']
            jump=abs(g['center_x']-pg['center_x'])
            width=max(o['box'][2],previous['box'][2])
            if gap>max_gap or jump>max(.3,3*width):
                tracks.append(current);current=[]
        current.append(o)
    if current:tracks.append(current)
    return tracks


def stationary_observations(track: list[dict], calibration: dict) -> list[dict]:
    """Use detected bottle motion; no fixed station coordinate or event timing."""
    if len(track)<3:return []
    ts=np.array([o['t'] for o in track]);xs=np.array([o['measurement']['geometry']['center_x'] for o in track])
    widths=np.array([o['box'][2] for o in track])
    # A short robust smoother suppresses per-frame segmentation jitter only.
    smooth=np.array([np.median(xs[np.abs(ts-t)<=.17]) for t in ts])
    stable=[]
    for i,t in enumerate(ts):
        lo=max(0,int(np.searchsorted(ts,t-.5)));hi=min(len(ts)-1,int(np.searchsorted(ts,t+.5)))
        elapsed=ts[hi]-ts[lo]
        speed=abs(smooth[hi]-smooth[lo])/elapsed if elapsed>0 else float('inf')
        stable.append(speed<=calibration['stationary_speed_bottle_widths_per_second']*widths[i])
    runs=[];run=[]
    for o,yes in zip(track,stable):
        if yes:
            if run and o['t']-run[-1]['t']>1.0:runs.append(run);run=[]
            run.append(o)
        elif run:runs.append(run);run=[]
    if run:runs.append(run)
    if not runs:return []
    best=max(runs,key=lambda r:r[-1]['t']-r[0]['t'])
    return best if best[-1]['t']-best[0]['t']>=calibration['stationary_seconds_min'] else []


def inspect_mask_cycle(track: list[dict], stationary: list[dict], number: int,
                       reference: float, calibration: dict, sample_fps: float) -> dict:
    """A low rising bottle is not underfilled; wait for plateau plus departure."""
    cid=f'cycle-{number:02d}';start,end=track[0]['t'],track[-1]['t']
    result={'id':cid,'label':f'Bottle {number}','start_time':start,'end_time':end,
        'fill_start_time':None,'fill_end_time':None,'measurement_time':None,'final_level':None,
        'status':'uncertain','confidence':0.,'reference_level':reference,'tolerance':calibration['tolerance'],
        'overflow_time':None,'reason':'No completed, settled RF-mask filling cycle could be established.',
        'evidence_start':start,'evidence_end':end,
        'completion_method':'RF-mask liquid rise, stable level, then detected bottle departure',
        'measurement_engine':'rfdetr','overflow_engine':OVERFLOW_ENGINE,
        'stationary_start':stationary[0]['t'],'stationary_end':stationary[-1]['t']}
    center=float(np.median([o['measurement']['geometry']['center_x'] for o in stationary]))
    width=float(np.median([o['box'][2] for o in stationary]))
    departure=next((o for o in track if o['t']>stationary[-1]['t'] and
                    abs(o['measurement']['geometry']['center_x']-center)>.35*width),None)
    result['departure_time']=departure['t'] if departure else None
    good=[o for o in stationary if o['measurement'].get('level') is not None and o['measurement'].get('confidence',0)>=.65]
    if good:
        final=good[-1];tail=[final]
        for o in reversed(good[:-1]):
            levels=[x['measurement']['level'] for x in tail]+[o['measurement']['level']]
            if tail[0]['t']-o['t']>max(.15,1.6/sample_fps) or max(levels)-min(levels)>calibration['settled_level_range_max']:break
            tail.insert(0,o)
        enough=tail[-1]['t']-tail[0]['t']>=calibration['settled_seconds_min']
        current=stationary[-1]['t']-tail[-1]['t']<=max(.3,1/sample_fps)
        level=float(np.median([o['measurement']['level'] for o in tail]))
        early=[o for o in good if o['t']<tail[0]['t'] and o['measurement']['level']<=level-calibration['observed_rise_min']]
        # A low early model value must persist; one isolated fragment is insufficient.
        rise=False; rising_run=[]
        for o in early:
            if rising_run and o['t']-rising_run[-1]['t']>max(1.,1.6/sample_fps):rising_run=[]
            rising_run.append(o)
            if o['t']-rising_run[0]['t']>=.25:rise=True;break
        if enough and current and rise and departure:
            result.update(fill_start_time=early[0]['t'],fill_end_time=tail[0]['t'],
                measurement_time=tail[len(tail)//2]['t'],final_level=round(level,5),
                confidence=round(float(np.median([o['measurement']['confidence'] for o in tail])),5))
            result['status']='underfill' if level<reference-calibration['tolerance'] else 'normal'
            result['reason']=('RF-mask liquid height settled below the reviewed normal reference minus demo tolerance before bottle departure.'
                if result['status']=='underfill' else 'RF-mask liquid height settled within the reviewed lower fill limit before bottle departure.')
            result.update(evidence_start=max(start,tail[0]['t']-.5),evidence_end=min(end,departure['t']+.5))
    # Exterior evidence is independent of interior level and mask-derived verdict.
    run=[];overflow=None
    for o in stationary:
        if o.get('exterior_pixels',0)>=calibration['exterior']['pixels_min']:
            if run and o['t']-run[-1]['t']>max(.15,1.6/sample_fps):run=[]
            run.append(o)
            if o['t']-run[0]['t']>=calibration['exterior']['persistence_seconds']:
                overflow=run[0];break
        else:run=[]
    if overflow:
        result.update(status='overflow',overflow_time=overflow['t'],
            reason='Persistent exterior orange liquid was detected by the separate calibrated color signal; RF-mask fill height alone is not evidence of overflow.',
            confidence=round(float(np.mean([min(1.,o['exterior_pixels']/(calibration['exterior']['pixels_min']*2)) for o in run])),5),
            evidence_start=max(start,overflow['t']-.75),evidence_end=min(end,overflow['t']+3.),
            overflow_evidence={'engine':OVERFLOW_ENGINE,'start':run[0]['t'],'end':run[-1]['t'],
                'minimum_exterior_pixels':min(o['exterior_pixels'] for o in run)})
    return result


def _exterior_evidence(video_path: str, observations: list[dict], config: dict, fps: float,
                       progress_callback: Callable | None=None) -> None:
    """Read only exterior regions for spill evidence, with no fill-height output."""
    cap=cv2.VideoCapture(video_path)
    if not cap.isOpened():raise ValueError('Cannot decode selected source for exterior evidence')
    targets={int(round(o['t']*fps)):o for o in observations}
    n=0;last=max(targets,default=0);done=0
    try:
        while n<=last:
            ok,frame=cap.read()
            if not ok:break
            o=targets.get(n)
            if o is not None:
                resized=cv2.resize(frame,tuple(config['reference_size']),interpolation=cv2.INTER_AREA)
                hsv=cv2.cvtColor(resized,cv2.COLOR_BGR2HSV)
                mask=cv2.inRange(hsv,tuple(config['orange_hsv_lower']),tuple(config['orange_hsv_upper']))>0
                o['exterior_pixels']=sum(int(mask[y:y+h,x:x+w].sum()) for x,y,w,h in config['boxes'])
                done+=1
            if progress_callback and n%240==0:progress_callback(.35+.5*n/max(1,last))
            n+=1
    finally:cap.release()
    if done!=len(targets):raise ValueError('Source decoding ended before all sampled exterior evidence frames')


def analyze_segmentation(segmentation_result: dict, video_path: str,
                         progress_callback: Callable | None=None,
                         approved_reference: dict | None=None) -> dict:
    started=time.monotonic();calibration=json.loads(CALIBRATION_PATH.read_text())
    digest=_sha(video_path)
    if digest!=segmentation_result.get('source_sha256'):raise ValueError('Segmentation source SHA does not match the selected source file')
    reviewed=calibration['reviewed_source_sha256']
    if digest not in reviewed:
        from .media_identity import validate_approved_reference
        if not any(validate_approved_reference(approved_reference,digest,reference) for reference in reviewed):
            raise ValueError('RF-mask cycle calibration is approved only for the reviewed single-station recording')
    models=segmentation_result.get('models',{})
    hashes={name:models.get(name,{}).get('checkpoint_sha256') for name in ('bottle','liquid')}
    if hashes!=calibration['model_hashes']:raise ValueError('Model checkpoints do not match the reviewed RF-mask calibration')
    cap=cv2.VideoCapture(video_path)
    width,height=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    source_fps=float(cap.get(cv2.CAP_PROP_FPS));duration=float(cap.get(cv2.CAP_PROP_FRAME_COUNT))/source_fps
    cap.release()
    fps=float(segmentation_result['sample_fps'])
    observations=[]
    for index,s in enumerate(segmentation_result['samples']):
        if not 0<=s['t']<duration+.001:raise ValueError('Model sample lies outside the source recording')
        candidates=[i for i in s.get('instances',[]) if i.get('bottle_confidence',0)>=.4]
        instance=max(candidates,key=lambda i:i['bottle_confidence'],default=None)
        m=measure_instance(instance,width,height) if instance else None
        observations.append({'t':float(s['t']),'index':index,'instance':instance,'measurement':m,
                             'box':instance['box'] if instance else []})
        if progress_callback and index%100==0:progress_callback(.35*index/max(1,len(segmentation_result['samples'])))
    reference_obs=min(observations,key=lambda o:abs(o['t']-calibration['normal_reference_time']))
    if abs(reference_obs['t']-calibration['normal_reference_time'])>1/fps or not reference_obs['measurement'] or reference_obs['measurement'].get('level') is None:
        raise ValueError('Reviewed normal reference has no usable RF-mask height; no fallback reference is allowed')
    reference=reference_obs['measurement']['level']
    _exterior_evidence(video_path,observations,calibration['exterior'],source_fps,progress_callback)
    samples=[{'t':o['t'],'scene_id':'single-station','phase':'bottle_moving' if o['measurement'] else 'station_empty','bottles':[]} for o in observations]
    cycles=[];events=[]
    for track in associate_tracks(observations):
        stationary=stationary_observations(track,calibration)
        if not stationary:continue
        cycle=inspect_mask_cycle(track,stationary,len(cycles)+1,reference,calibration,fps);cycles.append(cycle)
        for o in track:
            m=o['measurement'];inst=o['instance']
            if not m:continue
            if o['t']<stationary[0]['t'] or o['t']>stationary[-1]['t']:phase='bottle_moving'
            elif cycle['fill_end_time'] is not None and o['t']>=cycle['fill_end_time']:phase='settled'
            elif m['level'] is not None:phase='filling'
            else:phase='awaiting_fill'
            samples[o['index']].update(phase=phase,bottles=[{'id':cycle['id'],'label':cycle['label'],
                'box':inst['box'],'measurement_box':m['measurement_box'],'level':m['level'],
                'confidence':m['confidence'],'level_kind':m['level_kind'],'surface':m['surface'],
                'mask':max(inst.get('liquid_mask',[]),key=len,default=[]),
                'bottle_mask':inst.get('bottle_mask',[]),'liquid_mask':inst.get('liquid_mask',[]),
                'measurement_state':m['measurement_state'],'measurement_engine':'rfdetr','phase':phase}])
        event_time=cycle['overflow_time'] if cycle['overflow_time'] is not None else cycle['measurement_time']
        events.append({'id':cycle['id']+'-inspection','t':event_time if event_time is not None else cycle['end_time'],
            'type':'cycle_inspection','label':cycle['label']+': '+cycle['status'],'detail':cycle['reason'],'bottle_id':cycle['id']})
    pipeline=segmentation_result.get('pipeline_sha256') or segmentation_result.get('provenance',{}).get('pipeline_sha256')
    measurement={'engine':'rfdetr','algorithm':ALGORITHM,'model_hashes':hashes,
        'segmentation_pipeline_sha256':pipeline,'overflow_engine':OVERFLOW_ENGINE,
        'reference_level':reference,'reference_time':reference_obs['t'],'tolerance':calibration['tolerance'],
        'completion_method':'Model-mask motion, visible liquid rise, stable height and observed departure',
        'height_method':'Median liquid-mask upper boundary relative to detected bottle neck-to-base height'}
    colors=['#76b900','#61b7ff','#ffb946','#df8bea']
    result={'version':VERSION,'algorithm':ALGORITHM,'analysis_kind':'bottle-cycles','source_sha256':digest,
        'source_id':segmentation_result.get('source_id'),'source_clock_origin':segmentation_result.get('source_clock_origin'),
        'stream_id':segmentation_result.get('stream_id'),'sample_fps':fps,'source_fps':source_fps,
        'calibration_sha256':_sha(CALIBRATION_PATH),'runtime_seconds':round(time.monotonic()-started,3),
        'measurement':measurement,'models':models,'samples':samples,'cycles':cycles,'events':events,
        'bottles':[{'id':c['id'],'label':c['label'],'scene_id':'single-station','color':colors[i%4]} for i,c in enumerate(cycles)],
        'summary':{'total':len(cycles),**{s:sum(c['status']==s for c in cycles) for s in ('normal','underfill','overflow','uncertain')}},
        'quality':{'mode':'analyzed-replay','measurement_engine':'rfdetr','overflow_engine':OVERFLOW_ENGINE,
            'measurement':'RF-mask relative visible height; not volume','source_specific_calibration':True,
            'reference_level':reference,'tolerance':calibration['tolerance'],'reference_basis':calibration['normal_reference_basis'],
            'mask':'Learned RF-DETR bottle and liquid masks; source-adapted weak supervision for liquid.',
            'tracking':'Detected bottle-center continuity and observed stationarity; no seeded cycle times or count.',
            'completion':measurement['completion_method'],
            'confidence':'Heuristic model/geometry quality, not a calibrated probability of correctness.',
            'overflow':calibration['exterior']['basis'],
            'limits':['Visible height is not bottle volume or a production quality specification.',
                'Liquid model and normal reference are adapted to this fixed-camera source with weak supervision.',
                'Fill start/end mean observed liquid rise/settling; they are not direct nozzle-valve telemetry.',
                'Missing or unstable model masks remain uncertain; no color-based fill fallback.',
                'Overflow is a separate calibrated exterior-color signal, not an RF-DETR spill classification.',
                'Recorded replay analysis; no continuous live input in this component.']},
        'provenance':{'mode':'analyzed-replay','models':models,'segmentation_pipeline_sha256':pipeline,
            'segmentation_cache_key':segmentation_result.get('provenance',{}).get('cache_key'),
            'source_sha256':digest,'calibration_sha256':_sha(CALIBRATION_PATH),
            'normal_reference':{'time':reference_obs['t'],'level':reference,'basis':calibration['normal_reference_basis']}}}
    if approved_reference:result['media_identity']=approved_reference
    if progress_callback:progress_callback(1.)
    return result
