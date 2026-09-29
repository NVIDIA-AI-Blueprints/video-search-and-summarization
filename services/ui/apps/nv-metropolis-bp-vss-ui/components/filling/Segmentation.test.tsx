// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import React, {createRef} from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { exactSegmentationSample, FrameSynchronizedSegmentation, SegmentationOverlay, useSegmentation, type SegmentationResult } from './Segmentation';
import type { Source } from './types';
import * as apiModule from './api';

const source: Source = {id:'fixture-source',sha256:'fixture-sha',name:'Fixture',duration:10,width:1280,height:720,fps:24,media_url:'/fixture.mp4',mode:'recorded',chapters:[]};
const polygon: [number,number][] = [[.1,.1],[.2,.1],[.2,.4],[.1,.4]];
const result: SegmentationResult = {schema_version:2,source_id:source.id,source_sha256:source.sha256,sample_fps:24,source_fps:24,models:{bottle:{name:'Test fixture',checkpoint_sha256:'a'},liquid:{name:'Test fixture',checkpoint_sha256:'b'}},device:'fixture',runtime_seconds:0,provenance:{mode:'test-fixture'},samples:[
  {t:1,frame_index:24,instances:[{id:'fixture-1',box:[.1,.1,.1,.3],bottle_mask:[polygon],liquid_mask:[polygon],bottle_confidence:.9,liquid_confidence:.8}]},
  {t:25/24,frame_index:25,instances:[]},
]};

test('exact model frame is bound to both source ID and SHA',()=>{
  expect(exactSegmentationSample(result,source,1)?.t).toBe(1);
  expect(exactSegmentationSample(result,{...source,id:'different'},1)).toBeUndefined();
  expect(exactSegmentationSample(result,{...source,sha256:'different'},1)).toBeUndefined();
});
test('stale masks and invalid sample cadence remain absent',()=>{
  expect(exactSegmentationSample(result,source,8)).toBeUndefined();
  expect(exactSegmentationSample(result,source,0)).toBeUndefined();
  expect(exactSegmentationSample({...result,sample_fps:0},source,1)).toBeUndefined();
  expect(exactSegmentationSample(result,source,Number.NaN)).toBeUndefined();
});
test('empty newer detection sample does not retain an earlier bottle mask',()=>{
  expect(exactSegmentationSample(result,source,25/24)?.instances).toEqual([]);
});
test('model overlays toggle independently with normalized full-frame geometry',()=>{
  const view=render(<SegmentationOverlay sample={result.samples[0]} source={source} showBottle showLiquid/>);
  expect(view.container.querySelectorAll('polygon')).toHaveLength(2);
  expect(view.container.querySelector('polygon')?.getAttribute('points')).toContain('128,72');
  view.rerender(<SegmentationOverlay sample={result.samples[0]} source={source} showBottle={false} showLiquid/>);
  expect(view.container.querySelectorAll('polygon')).toHaveLength(1);
  expect(view.container.querySelector('polygon')?.getAttribute('fill')).toBe('#ff9e36');
  view.rerender(<SegmentationOverlay sample={result.samples[0]} source={source} showBottle showLiquid={false}/>);
  expect(view.container.querySelectorAll('polygon')).toHaveLength(1);
  expect(view.container.querySelector('polygon')?.getAttribute('stroke')).toBe('#54b9ff');
});
test('boxes are never substituted for missing model masks',()=>{
  const sample={t:1,frame_index:24,instances:[{...result.samples[0].instances[0],bottle_mask:[],liquid_mask:[]}]};
  const view=render(<SegmentationOverlay sample={sample} source={source} showBottle showLiquid/>);
  expect(view.container.querySelectorAll('polygon,rect')).toHaveLength(0);
});
test('invalid polygon coordinates do not enter SVG output',()=>{
  const sample={t:1,frame_index:24,instances:[{...result.samples[0].instances[0],bottle_mask:[[[1.2,.1],[.2,.2],[.3,.3]] as [number,number][]],liquid_mask:[]}]};
  const view=render(<SegmentationOverlay sample={sample} source={source} showBottle showLiquid/>);
  expect(view.container.querySelectorAll('polygon')).toHaveLength(0);
});


test('sparse legacy masks, wrong cadence and missing frame indices never borrow neighboring masks',()=>{
  expect(exactSegmentationSample({...result,schema_version:1},source,1)).toBeUndefined();
  expect(exactSegmentationSample({...result,sample_fps:2},source,1)).toBeUndefined();
  expect(exactSegmentationSample({...result,source_fps:30,sample_fps:30},source,1)).toBeUndefined();
  expect(exactSegmentationSample({...result,samples:[{...result.samples[0],frame_index:undefined}]},source,1)).toBeUndefined();
  expect(exactSegmentationSample(result,source,26/24)).toBeUndefined();
});

test('moving overlay follows decoded mediaTime, clears on seeking and never follows stale timeupdate',()=>{
  let nextCallback: VideoFrameRequestCallback | undefined;
  const request=jest.fn((callback:VideoFrameRequestCallback)=>{nextCallback=callback;return 1;});
  const cancel=jest.fn();
  Object.defineProperty(HTMLVideoElement.prototype,'requestVideoFrameCallback',{configurable:true,value:request});
  Object.defineProperty(HTMLVideoElement.prototype,'cancelVideoFrameCallback',{configurable:true,value:cancel});
  const video=createRef<HTMLVideoElement>();
  const view=render(<><video ref={video}/><FrameSynchronizedSegmentation video={video} result={result} source={source} showBottle showLiquid/></>);
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  act(()=>nextCallback?.(0,{mediaTime:1} as VideoFrameCallbackMetadata));
  expect(view.container.querySelector('.rf-segmentation-overlay')?.getAttribute('data-frame-index')).toBe('24');
  Object.defineProperty(video.current,'currentTime',{configurable:true,value:3});
  fireEvent.timeUpdate(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')?.getAttribute('data-frame-index')).toBe('24');
  fireEvent.seeking(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  act(()=>nextCallback?.(0,{mediaTime:25/24} as VideoFrameCallbackMetadata));
  expect(view.container.querySelector('.rf-segmentation-overlay')?.getAttribute('data-frame-index')).toBe('25');
  expect(view.container.querySelectorAll('polygon')).toHaveLength(0);
  act(()=>nextCallback?.(0,{mediaTime:26/24} as VideoFrameCallbackMetadata));
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  view.unmount();
  expect(cancel).toHaveBeenCalled();
  delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).requestVideoFrameCallback;
  delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).cancelVideoFrameCallback;
});


test('initial measurement metadata does not duplicate the model-result download; a changed cache key refreshes it',async()=>{
  const status={status:'complete',progress:1,source_id:source.id,source_sha256:source.sha256};
  const read=jest.spyOn(apiModule,'api').mockImplementation((async(path:string)=>path.includes('/result')?result:status) as typeof apiModule.api);
  function Harness({cacheKey}:{cacheKey?:string}){
    const state=useSegmentation(source,1,true,cacheKey);
    return <span data-testid="ready">{state.result?'ready':'loading'}</span>;
  }
  const view=render(<Harness/>);
  await waitFor(()=>expect(view.getByTestId('ready').textContent).toBe('ready'));
  const resultReads=()=>read.mock.calls.filter(([path])=>path.includes('/result')).length;
  expect(resultReads()).toBe(1);
  view.rerender(<Harness cacheKey="first-cache"/>);
  await act(async()=>{await Promise.resolve();});
  expect(resultReads()).toBe(1);
  view.rerender(<Harness cacheKey="replacement-cache"/>);
  await waitFor(()=>expect(resultReads()).toBe(2));
  view.unmount();read.mockRestore();
});

test('paused seek restores its exact decoded callback frame after seeking clears and rejects an old frame',()=>{
  let nextCallback:VideoFrameRequestCallback|undefined,seeking=false;
  Object.defineProperty(HTMLVideoElement.prototype,'requestVideoFrameCallback',{configurable:true,value:(callback:VideoFrameRequestCallback)=>{nextCallback=callback;return 1;}});
  Object.defineProperty(HTMLVideoElement.prototype,'cancelVideoFrameCallback',{configurable:true,value:jest.fn()});
  const video=createRef<HTMLVideoElement>();
  const view=render(<><video ref={video}/><FrameSynchronizedSegmentation video={video} result={result} source={source} showBottle showLiquid/></>);
  Object.defineProperty(video.current,'seeking',{configurable:true,get:()=>seeking});
  Object.defineProperty(video.current,'readyState',{configurable:true,value:2});
  video.current!.currentTime=1;
  seeking=true;fireEvent.seeking(video.current!);
  act(()=>nextCallback?.(0,{mediaTime:1} as VideoFrameCallbackMetadata));
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  seeking=false;fireEvent.seeked(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')?.getAttribute('data-frame-index')).toBe('24');
  // A seek truncated to microseconds can legitimately display the preceding frame.
  video.current!.currentTime=1+1/24-1e-6;
  seeking=true;fireEvent.seeking(video.current!);
  act(()=>nextCallback?.(0,{mediaTime:1} as VideoFrameCallbackMetadata));
  seeking=false;fireEvent.seeked(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')?.getAttribute('data-frame-index')).toBe('24');
  video.current!.currentTime=1+1/24;
  seeking=true;fireEvent.seeking(video.current!);
  seeking=false;fireEvent.seeked(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  // The decoded target frame may arrive before the seeking event is dispatched.
  video.current!.currentTime=25/24;
  act(()=>nextCallback?.(0,{mediaTime:25/24} as VideoFrameCallbackMetadata));
  seeking=true;fireEvent.seeking(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  seeking=false;fireEvent.seeked(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')?.getAttribute('data-frame-index')).toBe('25');
  video.current!.currentTime=3;
  seeking=true;fireEvent.seeking(video.current!);
  act(()=>nextCallback?.(0,{mediaTime:1} as VideoFrameCallbackMetadata));
  seeking=false;fireEvent.seeked(video.current!);
  expect(view.container.querySelector('.rf-segmentation-overlay')).toBeNull();
  view.unmount();
  delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).requestVideoFrameCallback;
  delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).cancelVideoFrameCallback;
});
