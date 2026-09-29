// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import { currentPresentation, liveBottleLabel, liveBottleQuestion, liveChatContext, isLiveFrame, PairedFrameQueue, paintLiveFrame, safeEvidenceUrl, type DecodedFrame, type LiveFrame } from './liveFrames';

const fixture = (frame_id = 1): LiveFrame => ({type:'frame',session_id:'session-a',stream_id:'stream-a',frame_id,source_pts_seconds:frame_id/24,epoch:0,received_at_utc:'2026-09-23T00:00:00Z',width:1280,height:720,preview_width:1280,preview_height:720,jpeg_base64:'fixture-jpeg',instances:[{id:'bottle',box:[.1,.1,.2,.5],bottle_confidence:.9,liquid_confidence:.8,bottle_mask:[[[.1,.1],[.3,.1],[.3,.6]]],liquid_mask:[[[.1,.4],[.3,.4],[.3,.6]]]}],current:{level:frame_id/10},stats:{},summary:{total:0,normal:0,underfill:0,overflow:0,uncertain:0}});
const decoded = (): DecodedFrame => ({image:{} as CanvasImageSource,width:1280,height:720,close:jest.fn()});
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };

test('accepts only complete frames belonging to the exact live session and stream', () => {
  const scope={session_id:'session-a',stream_id:'stream-a'};
  expect(isLiveFrame(fixture(),scope)).toBe(true);
  expect(isLiveFrame({...fixture(),session_id:'previous'},scope)).toBe(false);
  expect(isLiveFrame({...fixture(),stream_id:'another-camera'},scope)).toBe(false);
  expect(isLiveFrame({...fixture(),width:0},scope)).toBe(false);
  expect(isLiveFrame({...fixture(),frame_id:Infinity},scope)).toBe(false);
  expect(isLiveFrame({...fixture(),instances:undefined},scope)).toBe(false);
  expect(isLiveFrame({...fixture(),jpeg_base64:''},scope)).toBe(false);
});

test('coalesces slow-viewer frames while preserving each decoded image and its own measurements', async () => {
  const resolvers: ((result:DecodedFrame)=>void)[]=[];
  const decode=jest.fn(()=>new Promise<DecodedFrame>(resolve=>resolvers.push(resolve)));
  const publish=jest.fn(), fail=jest.fn();
  const queue=new PairedFrameQueue(decode,publish,fail);
  queue.push(fixture(1)); queue.push(fixture(2)); queue.push(fixture(3));
  const first=decoded(); resolvers[0](first); await tick();
  expect(publish).toHaveBeenNthCalledWith(1,expect.objectContaining({frame_id:1,current:{level:.1}}),first);
  expect(decode).toHaveBeenNthCalledWith(2,expect.objectContaining({frame_id:3}));
  expect(queue.dropped).toBe(1);
  const third=decoded(); resolvers[1](third); await tick();
  expect(publish).toHaveBeenNthCalledWith(2,expect.objectContaining({frame_id:3,current:{level:.3}}),third);
  queue.push(fixture(2)); expect(decode).toHaveBeenCalledTimes(2);
  expect(fail).not.toHaveBeenCalled();
});

test('session teardown discards an in-flight image rather than painting into the next source', async () => {
  let resolve!: (result:DecodedFrame)=>void;
  const publish=jest.fn();
  const queue=new PairedFrameQueue(()=>new Promise(r=>{resolve=r;}),publish,jest.fn());
  queue.push(fixture()); queue.dispose();
  const image=decoded(); resolve(image); await tick();
  expect(publish).not.toHaveBeenCalled(); expect(image.close).toHaveBeenCalledTimes(1);
});

test('dimension mismatch refuses inference polygons even when the JPEG decoded', async () => {
  const image={...decoded(),width:640}, publish=jest.fn(), fail=jest.fn();
  const queue=new PairedFrameQueue(async()=>image,publish,fail);
  queue.push(fixture()); await tick();
  expect(publish).not.toHaveBeenCalled(); expect(fail).toHaveBeenCalledWith(expect.objectContaining({message:expect.stringContaining('dimensions')}));
  expect(image.close).toHaveBeenCalled();
});

test('canvas draws the actual image before its masks; empty detections clear prior geometry', () => {
  const context={drawImage:jest.fn(),beginPath:jest.fn(),moveTo:jest.fn(),lineTo:jest.fn(),closePath:jest.fn(),fill:jest.fn(),stroke:jest.fn()};
  const canvas={width:0,height:0,dataset:{},getContext:()=>context} as unknown as HTMLCanvasElement;
  const image=decoded();
  paintLiveFrame(canvas,fixture(),image,true,true);
  expect(context.drawImage).toHaveBeenCalledWith(image.image,0,0,1280,720);
  expect(context.drawImage.mock.invocationCallOrder[0]).toBeLessThan(context.beginPath.mock.invocationCallOrder[0]);
  expect(context.stroke).toHaveBeenCalledTimes(2);
  expect(canvas.dataset).toEqual({frameId:'1',sourcePts:String(1/24),sessionId:'session-a',streamId:'stream-a',epoch:'0'});
  context.stroke.mockClear();
  paintLiveFrame(canvas,{...fixture(2),instances:[]},image,true,true);
  expect(context.drawImage).toHaveBeenCalledTimes(2); expect(context.stroke).not.toHaveBeenCalled();
});

test('mask toggles paint independent geometry without changing the displayed source frame', () => {
  const context={drawImage:jest.fn(),beginPath:jest.fn(),moveTo:jest.fn(),lineTo:jest.fn(),closePath:jest.fn(),fill:jest.fn(),stroke:jest.fn()};
  const canvas={width:0,height:0,dataset:{},getContext:()=>context} as unknown as HTMLCanvasElement;
  paintLiveFrame(canvas,fixture(),decoded(),false,true);
  expect(context.stroke).toHaveBeenCalledTimes(1); expect(context.fill).toHaveBeenCalledTimes(1);
  context.stroke.mockClear(); context.fill.mockClear();
  paintLiveFrame(canvas,fixture(),decoded(),true,false);
  expect(context.stroke).toHaveBeenCalledTimes(1); expect(context.fill).not.toHaveBeenCalled();
});

test('pending evidence remains unavailable and only supplied HTTP media links are rendered', () => {
  const event={event_id:'bottle-1',session_id:'session-a',stream_id:'stream-a',epoch:0,seq:1,kind:'cycle.finalized',evidence_status:'pending',video_evidences:[]};
  expect(safeEvidenceUrl(event)).toBeNull();
  expect(safeEvidenceUrl({...event,video_evidences:[{public_url:'javascript:alert(1)'}]})).toBeNull();
  expect(safeEvidenceUrl({...event,video_evidences:[{public_url:'/filling/api/evidence/actual.mp4'}]})).toBe('/filling/api/evidence/actual.mp4');
});


test('a reconnect epoch cannot show an old in-flight frame or suppress reset frame indices', async () => {
  const resolvers: ((result:DecodedFrame)=>void)[]=[];
  const publish=jest.fn();
  const queue=new PairedFrameQueue(()=>new Promise(resolve=>resolvers.push(resolve)),publish,jest.fn());
  queue.push(fixture(900)); queue.push({...fixture(1),epoch:1});
  const old=decoded(); resolvers[0](old); await tick();
  expect(publish).not.toHaveBeenCalled(); expect(old.close).toHaveBeenCalled();
  const current=decoded(); resolvers[1](current); await tick();
  expect(publish).toHaveBeenCalledWith(expect.objectContaining({epoch:1,frame_id:1}),current);
  queue.push(fixture(901)); expect(resolvers).toHaveLength(2);
});

test('preview resizing preserves normalized full-frame masks', async () => {
  const image={...decoded(),width:960,height:540};
  const publish=jest.fn(),fail=jest.fn();
  const queue=new PairedFrameQueue(async()=>image,publish,fail);
  queue.push({...fixture(),preview_width:960,preview_height:540}); await tick();
  expect(publish).toHaveBeenCalledTimes(1); expect(fail).not.toHaveBeenCalled();
});


test('live chat binds actual identity, preserves target epoch and never treats event seq as bottle ID', () => {
  const session = {session_id:'session-a',stream_id:'stream-a',status:'running',epoch:4};
  expect(liveChatContext(session,4)?.data).toEqual({mode:'live',scope:'selected_live_session',session_id:'session-a',stream_id:'stream-a',view_epoch:4,selected_bottles:[]});
  expect(liveChatContext({status:'idle'},4)).toBeNull();
  expect(liveChatContext(session,undefined)).toBeNull();
  const question=liveBottleQuestion(session,'session-a:2:cycle-305',2,'actual-event');
  expect(question).toContain('bottle cycle-305');
  expect(question).toContain('epoch 2');
  expect(question).toContain('exact track ID session-a:2:cycle-305');
  expect(question).toContain('event ID actual-event');
  expect(liveBottleLabel('session-a:2:cycle-22')).toBe('cycle-22');
});

test('overflow, obscured and departing frames withhold raw height; readable frames remain unsmoothed', () => {
  expect(currentPresentation({level:.91,phase:'overflow_observed',overflow:{observed:true,engine:'exterior',provisional:true}})).toMatchObject({level:null,title:'Overflow observed'});
  expect(currentPresentation({level:.91,quality:{readable:false,reason:'Boundary obscured'}})).toMatchObject({level:null,title:'Measurement obscured',description:'Boundary obscured'});
  expect(currentPresentation({level:.91,phase:'departing'})).toMatchObject({level:null,title:'Bottle departing'});
  expect(currentPresentation({level:.41,quality:{readable:true}}).level).toBe(.41);
  expect(currentPresentation({level:.42,quality:{readable:true}}).level).toBe(.42);
  expect(currentPresentation({level:null}).level).toBeNull();
  expect(currentPresentation(null).level).toBeNull();
});

import {bottleSelection, toggleBottleSelection} from './liveFrames';

describe('visible bottle selection', () => {
  const session = {session_id: 'session', stream_id: 'camera', status: 'running'};
  it('freezes current frame identity and retains an older selected epoch', () => {
    const selected = bottleSelection(session, 'cycle-12', 2, undefined, 50)!;
    const context = liveChatContext(session, 3, [selected]);
    expect(context?.data.selected_bottles).toEqual([{track_id: 'session:epoch-2:cycle-12', epoch: 2, frame_id: 50}]);
    expect(context?.data.view_epoch).toBe(3);
  });
  it('allows at most two distinct selections and removes an explicit deselection', () => {
    const a = bottleSelection(session, 'cycle-1', 1)!;
    const b = bottleSelection(session, 'cycle-2', 1)!;
    const c = bottleSelection(session, 'cycle-3', 1)!;
    expect(toggleBottleSelection([a,b], c)).toEqual([a,b]);
    expect(toggleBottleSelection([a,b], a)).toEqual([b]);
  });
  it('does not attach another session or manufacture a missing target', () => {
    expect(bottleSelection(session, 'other:epoch-1:cycle-1', 1)).toBeNull();
    const selected = bottleSelection(session, 'cycle-1', 1)!;
    expect(liveChatContext({...session, session_id: 'other'}, 1, [selected])?.data.selected_bottles).toEqual([]);
    expect(liveChatContext(session, 1)?.data.selected_bottles).toEqual([]);
  });
});
