// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import D1AgentPanel from '../../components/D1AgentPanel';

let mockFeedProps: any;
jest.mock('../../components/SourcePreview', () => ({ __esModule: true, default: ({ streamId, revision, paused }: any) => <div aria-label="Current monitored video" data-stream={streamId} data-revision={revision} data-paused={String(paused)} /> }));
jest.mock('../../components/CosmosReviewFeed', () => ({ __esModule: true, default: (props: any) => {
 mockFeedProps = props;
 return <div aria-label="Cosmos review feed" data-count={props.events.length} data-available={String(props.available)} />;
} }));

const now = 1794600000;
const warehouse = { id:'recorded-warehouse', revision:1, config:{enabled:false,interval_seconds:2,question:{instructions:'Warehouse question',type:'noul',criteria:null}} };
const traffic = { id:'d1-live', revision:2, config:{enabled:true,interval_seconds:2,question:{instructions:'Is there a traffic queue?',type:'noul',criteria:null}} };
const rule = { id:'d1-live-rule',revision:3,config:{stream_id:traffic.id,enabled:true,config_revision:2,review_prompt:'Explain traffic',field:'noul',value:.8,operator:'gte',consecutive_samples:3,cooldown_seconds:60,pre_seconds:15,post_seconds:5}};
const event = (id='one') => ({id,stream_id:traffic.id,captured_at:now-20,clip_start:now-35,clip_end:now-15,rule_id:rule.id,rule_revision:3,rule:rule.config,trigger:{config_revision:2,question:traffic.config.question,answer:{noul:.95}}});
const review = (id='one',verdict='confirmed') => ({event_id:id,stream_id:traffic.id,verification_version:1,verdict,summary:'A slow queue is visible.',evidence:['Vehicles remain closely spaced.'],uncertainty:['The cause is outside the view.'],clip_sha256:'abc'});
const candidate = (id='one',status='confirmed') => ({event_id:id,incident_id:'incident-one',stream_id:traffic.id,captured_at:now-20,status,event:event(id),review:status==='failed'||status==='pending'?null:review(id,status),clip_ready:status!=='pending',error:status==='failed'?'InvalidVerificationOutput':null});
const incident = () => ({id:'incident-one',stream_id:traffic.id,status:'active',monitoring_status:'observing',started_at:now-200,last_seen:now-20,candidate_count:4,confirmed_count:4,representative_event_id:'one',latest_event_id:'one',latest_status:'confirmed',candidate_ids:['one'],event:event(),review:review(),question:traffic.config.question});
function snapshot():any { return JSON.parse(JSON.stringify({fetched_at:new Date(now*1000).toISOString(),health:{data:{ready:true}},assistant:{data:{ready:true}},streams:{data:[warehouse,traffic]},rules:{data:[rule]},decisions:{data:[{stream_id:warehouse.id,question:{instructions:'Wrong warehouse question'}},{stream_id:traffic.id,captured_at:now-1,question:{instructions:'Actual traffic check'},answer:{noul:.95},model:'LiquidAI/d1-2B',input:{mode:'video',frame_count:8,window_start:now-8,window_end:now-1}}]},jobs:{data:[]},events:{data:[]},reviews:{data:[]},incidents:{data:{incidents:[incident()],candidates:[candidate()],counts:{}}}})); }
const recordedSources = (active='live') => ({ data: { active_source:active, sources:[
 {id:'live',stream_id:'d1-live',label:'Live RTSP',kind:'live',available:true},
 {id:'queued',stream_id:'d1-queued',label:'Queued traffic',kind:'recorded',available:true},
 {id:'flowing',stream_id:'d1-flowing',label:'Flowing traffic',kind:'recorded',available:true},
] } });

describe('Two-section traffic monitoring',()=>{
 const originalFetch=global.fetch, originalClone=global.structuredClone;
 let current:any;
 beforeEach(()=>{jest.useFakeTimers();sessionStorage.clear();current=snapshot();mockFeedProps=null;global.structuredClone=value=>JSON.parse(JSON.stringify(value));global.fetch=jest.fn().mockImplementation(async()=>({ok:true,json:async()=>JSON.parse(JSON.stringify(current))})) as any;});
 afterEach(()=>{jest.useRealTimers();global.fetch=originalFetch;global.structuredClone=originalClone;});
 const setup=async()=>{render(<D1AgentPanel isActive/>);await screen.findByText('Actual traffic check');await act(async()=>{});};
 it('keeps current input and D1 output above separate Cosmos evidence even when an incident exists',async()=>{
  await setup();
  const live=screen.getByRole('region',{name:/D1 live inference/});
  const cosmos=screen.getByRole('region',{name:/Cosmos verification/});
  const preview=screen.getByLabelText('Current monitored video');
  expect(live).toContainElement(preview);
  expect(within(live).getByTestId('d1-question')).toHaveTextContent('Actual traffic check');
  expect(within(live).getByText('Yes (95% confidence)')).toBeInTheDocument();
  expect(cosmos).toContainElement(screen.getByLabelText('Cosmos review feed'));
  expect(cosmos).not.toContainElement(preview);
  expect(mockFeedProps.events[0]).toMatchObject({id:'one',review:{clip_sha256:'abc'}});
  expect(mockFeedProps.incidents[0]).toMatchObject({id:'incident-one',candidate_count:4});
  expect((global.fetch as jest.Mock).mock.calls.every(call=>!call[1]?.method||call[1]?.method==='GET')).toBe(true);
 });
 it('continues showing incoming video without manufacturing candidates or asking Cosmos when no event exists',async()=>{
  current.incidents.data={incidents:[],candidates:[]};
  current.rules.data[0].config.value=.85;
  current.decisions.data[1].answer.noul=.16;
  await setup();
  expect(screen.getByText('Video going into D1')).toBeInTheDocument();
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-stream',traffic.id);
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-revision','2');
  expect(screen.getByText('Yes with at least 85% confidence for 3 consecutive checks.')).toBeInTheDocument();
  expect(screen.getByText('No (84% confidence)')).toBeInTheDocument();
  expect(mockFeedProps.events).toEqual([]);
  expect(mockFeedProps).toMatchObject({available:true,autoReview:true});
  expect((global.fetch as jest.Mock).mock.calls.every(call=>!call[1]?.method||call[1]?.method==='GET')).toBe(true);
 });
 it('does not remount the monitored preview as new clips and verdicts arrive',async()=>{
  await setup();
  const preview=screen.getByLabelText('Current monitored video');
  current.incidents.data.candidates.unshift(candidate('two','uncertain'));
  Object.assign(current.incidents.data.incidents[0],{latest_event_id:'two',latest_status:'uncertain',candidate_ids:['two','one']});
  await act(async()=>{jest.advanceTimersByTime(5000);});
  expect(screen.getByLabelText('Current monitored video')).toBe(preview);
  expect(mockFeedProps.events.map((e:any)=>e.id)).toEqual(['two','one']);
  expect(mockFeedProps.events[0]).toMatchObject({status:'uncertain',review:{verdict:'uncertain'}});
  expect(mockFeedProps.events[1]).toMatchObject({status:'confirmed',review:{verdict:'confirmed'}});
 });
 it('passes only the active source’s candidates, incidents, jobs and reviews to Cosmos',async()=>{
  current.incidents.data.candidates.push({...candidate('warehouse-event'),stream_id:warehouse.id,event:{...event('warehouse-event'),stream_id:warehouse.id}});
  current.incidents.data.incidents.push({...incident(),id:'warehouse-incident',stream_id:warehouse.id});
  current.jobs.data=[{...event('other-job'),stream_id:warehouse.id,status:'complete'}];
  current.reviews.data=[{...review(),stream_id:warehouse.id,summary:'Wrong source review'}];
  await setup();
  expect(mockFeedProps.events).toHaveLength(1);
  expect(mockFeedProps.events[0]).toMatchObject({id:'one',stream_id:traffic.id,review:{summary:'A slow queue is visible.'}});
  expect(mockFeedProps.incidents.map((i:any)=>i.id)).toEqual(['incident-one']);
  expect(mockFeedProps.activeRule.id).toBe(rule.id);
  expect(mockFeedProps.streamRevision).toBe(traffic.revision);
 });
 it('retains all candidate verdict states instead of treating them as confirmed incidents',async()=>{
  current.incidents.data.incidents=[];
  current.incidents.data.candidates=[candidate('confirmed'),candidate('rejected','rejected'),candidate('uncertain','uncertain'),candidate('pending','pending'),candidate('failed','failed'),{...candidate('legacy','legacy'),review:{event_id:'legacy',context:'Earlier caption.'}}];
  await setup();
  expect(mockFeedProps.events.map((e:any)=>e.status)).toEqual(['confirmed','rejected','uncertain','pending','failed','legacy']);
  expect(mockFeedProps.events.find((e:any)=>e.id==='failed').review).toBeNull();
  expect(mockFeedProps.events.find((e:any)=>e.id==='legacy').review.verification_version).toBeUndefined();
  expect(mockFeedProps.incidents).toEqual([]);
 });
 it('keeps original saved trigger question and verdict separate from the newest D1 answer',async()=>{
  current.incidents.data.candidates[0].event={...event(),rule_revision:1,trigger:{config_revision:1,question:{instructions:'Original historical question'},answer:{noul:.9}}};
  await setup();
  expect(screen.getByTestId('d1-question')).toHaveTextContent('Actual traffic check');
  expect(mockFeedProps.events[0].incident.trigger.question.instructions).toBe('Original historical question');
  expect(mockFeedProps.events[0].review.clip_sha256).toBe('abc');
  expect(mockFeedProps.events[0].incident.rule_revision).toBe(1);
  expect(mockFeedProps.activeRule.revision).toBe(3);
 });
 it('includes an exact representative clip even after it leaves the bounded candidate list',async()=>{
  current.incidents.data.candidates=[candidate('two','pending')];
  await setup();
  const saved=mockFeedProps.events.find((e:any)=>e.id==='one');
  expect(saved).toMatchObject({id:'one',clip_start:now-35,clip_end:now-15,review:{event_id:'one',clip_sha256:'abc'},incident:{id:'one'}});
  expect(mockFeedProps.events.filter((e:any)=>e.id==='one')).toHaveLength(1);
 });
 it('exposes unavailable verification separately while preserving available source-bound evidence',async()=>{
  current.incidents={error:'Projection unavailable'};
  current.jobs.data=[{...event(),status:'complete',clip_ready:true}];
  current.events.data=[event()];current.reviews.data=[review()];
  await setup();
  expect(mockFeedProps.available).toBe(false);
  expect(mockFeedProps.events[0]).toMatchObject({id:'one',review:{event_id:'one'},incident:{id:'one'}});
  expect(screen.getByLabelText('Current monitored video')).toBeInTheDocument();
 });
 it('resets source-bound preview and feed when the monitored source changes',async()=>{
  await setup();
  const oldPreview=screen.getByLabelText('Current monitored video'),oldFeed=screen.getByLabelText('Cosmos review feed');
  current.sources=recordedSources('queued');
  current.streams.data=[warehouse,{...traffic,id:'d1-queued'}];
  await act(async()=>{jest.advanceTimersByTime(5000);});
  expect(screen.getByText(/Source: Queued traffic/)).toBeInTheDocument();
  expect(screen.getByText('Recorded input')).toBeInTheDocument();
  expect(screen.getByLabelText('Current monitored video')).not.toBe(oldPreview);
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-stream','d1-queued');
  expect(screen.getByLabelText('Cosmos review feed')).not.toBe(oldFeed);
  expect(mockFeedProps.events).toEqual([]);expect(mockFeedProps.incidents).toEqual([]);
 });
 it('pauses current input without dropping saved review data',async()=>{
  await setup();
  fireEvent.click(screen.getByRole('button',{name:'Pause updates'}));
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-paused','true');
  expect(mockFeedProps.events[0].id).toBe('one');
  fireEvent.click(screen.getByRole('button',{name:'Resume updates'}));
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-paused','false');
  await act(async()=>{});
 });
 it('pauses preview when the monitor tab is inactive',async()=>{
  const view=render(<D1AgentPanel isActive/>);await screen.findByText('Actual traffic check');
  view.rerender(<D1AgentPanel isActive={false}/>);
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-paused','true');
 });
 it('labels a stale D1 answer without the removed availability messages',async()=>{current.decisions.data[1].captured_at=now-100;await setup();expect(screen.getByText('Last D1 answer · out of date')).toBeInTheDocument();expect(screen.queryByText('Input unavailable')).not.toBeInTheDocument();expect(screen.queryByText('Current data is unavailable. Saved evidence remains available.')).not.toBeInTheDocument();expect(screen.queryByText('Monitoring',{exact:true})).not.toBeInTheDocument();});
 it('keeps admin prompt configuration secondary and preserves a draft while closing setup',async()=>{await setup();expect(screen.queryByLabelText('Describe your monitoring goal')).not.toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Monitoring setup'}));const input=screen.getByLabelText('Describe your monitoring goal');fireEvent.change(input,{target:{value:'Watch slow traffic'}});fireEvent.click(screen.getByRole('button',{name:'Close settings'}));expect(input).not.toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Monitoring setup'}));expect(screen.getByLabelText('Describe your monitoring goal')).toHaveValue('Watch slow traffic');});
 it('opens manual configuration when the optional prompt assistant is disabled', async () => {
  current.assistant = { data: { ready: false, configured: false } };
  await setup();
  fireEvent.click(screen.getByRole('button', {name: 'Monitoring setup'}));
  expect(screen.queryByRole('tab', {name: 'Prompt assistant'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('What should D1 watch for?')).toHaveValue('Is there a traffic queue?');
 });
 it('preserves advanced input configuration and binds it to the enabled stream',async()=>{await setup();fireEvent.click(screen.getByRole('button',{name:'Monitoring setup'}));fireEvent.click(screen.getByRole('tab',{name:'Source & trigger settings'}));expect(screen.getByLabelText('Stream',{exact:true})).toHaveValue(traffic.id);fireEvent.change(screen.getByLabelText('What D1 receives'),{target:{value:'video'}});expect(screen.getByLabelText('Frames per video check')).toHaveValue(8);expect(screen.getByLabelText('What should D1 watch for?')).toHaveValue('Is there a traffic queue?');});
 it('shows the current stream and all prepared inputs in one visible dropdown without switching automatically',async()=>{
  current.sources=recordedSources();await setup();
  const select=screen.getByRole('combobox',{name:'Stream',exact:true});
  expect(select).toBeVisible();expect(select).toHaveAttribute('id','demo-source');
  expect(select).toHaveValue('live');
  expect(within(select).getByRole('option',{name:'Live RTSP',exact:true})).toHaveValue('live');
  expect(within(select).getByRole('option',{name:'Queued traffic',exact:true})).toHaveValue('queued');
  expect(within(select).getByRole('option',{name:'Flowing traffic',exact:true})).toHaveValue('flowing');
  expect(screen.queryByRole('group',{name:'Recorded traffic examples'})).not.toBeInTheDocument();
  expect(screen.queryByText('Change source')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Use this source'})).not.toBeInTheDocument();
  current.decisions.data[1].captured_at=now-100;
  await act(async()=>{jest.advanceTimersByTime(5000);});
  expect(select).toHaveValue('live');
  expect((global.fetch as jest.Mock).mock.calls.some(call=>call[1]?.method==='POST')).toBe(false);
 });
 it.each([
  ['congested traffic','queued'],
  ['flowing traffic','flowing'],
 ])('requests %s immediately when its stream is selected',async(_label,target)=>{
  current.sources=recordedSources();await setup();
  fireEvent.change(screen.getByRole('combobox',{name:'Stream',exact:true}),{target:{value:target}});
  await act(async()=>{});
  const writes=(global.fetch as jest.Mock).mock.calls.filter(call=>call[1]?.method==='POST');
  expect(writes).toHaveLength(1);
  expect(writes[0]).toEqual(['/api/proxy/d1/demo/sources',expect.objectContaining({body:JSON.stringify({source_id:target})})]);
 });
 it('keeps the confirmed stream selected during a switch and blocks duplicate requests',async()=>{
  current.sources=recordedSources('queued');
  current.streams.data=[warehouse,{...traffic,id:'d1-queued'}];
  current.decisions.data[1].stream_id='d1-queued';
  const readFetch=global.fetch;let resolveSource!:(value:any)=>void;
  const response=new Promise(resolve=>{resolveSource=resolve;});
  global.fetch=jest.fn((url,init)=>init?.method==='POST'?response:readFetch(url,init)) as any;
  await setup();
  const select=screen.getByRole('combobox',{name:'Stream',exact:true});
  expect(select).toHaveValue('queued');expect(select).toBeEnabled();
  fireEvent.change(select,{target:{value:'flowing'}});
  expect(select).toBeDisabled();expect(select).toHaveValue('queued');
  expect(screen.getByText('Switching stream…')).toBeVisible();
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-paused','true');
  fireEvent.change(select,{target:{value:'live'}});
  expect((global.fetch as jest.Mock).mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1);
  current.sources.data.active_source='flowing';
  current.streams.data=[warehouse,{...traffic,id:'d1-flowing'}];
  current.decisions.data[1].stream_id='d1-flowing';
  await act(async()=>{resolveSource({ok:true,json:async()=>({active_source:'flowing'})});});
  expect(select).toHaveValue('flowing');expect(select).toBeEnabled();
  expect(screen.queryByText('Switching stream…')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-stream','d1-flowing');
  expect(screen.getByLabelText('Current monitored video')).toHaveAttribute('data-paused','false');
 });
 it('keeps the confirmed stream selected and exposes a failed switch beside the control',async()=>{
  current.sources=recordedSources('queued');
  const readFetch=global.fetch;
  global.fetch=jest.fn((url,init)=>init?.method==='POST'?Promise.resolve({ok:false,json:async()=>({error:'Source change failed. Try again.'})}):readFetch(url,init)) as any;
  await setup();
  const select=screen.getByRole('combobox',{name:'Stream',exact:true});
  fireEvent.change(select,{target:{value:'flowing'}});
  await act(async()=>{});
  expect(screen.getByRole('alert')).toHaveTextContent('Source change failed. Try again.');
  expect(screen.getByRole('alert')).toBeVisible();
  expect(select).toHaveValue('queued');expect(select).toBeEnabled();
  expect(screen.queryByText('Switching stream…')).not.toBeInTheDocument();
 });
 it('disables unavailable streams and ignores unavailable, missing or already active selections',async()=>{
  current.sources=recordedSources();current.sources.data.sources[2].available=false;
  await setup();
  const select=screen.getByRole('combobox',{name:'Stream',exact:true});
  expect(within(select).getByRole('option',{name:/Flowing traffic/})).toBeDisabled();
  fireEvent.change(select,{target:{value:'flowing'}});
  fireEvent.change(select,{target:{value:'missing-source'}});
  fireEvent.change(select,{target:{value:'live'}});
  await act(async()=>{});
  expect(select).toHaveValue('live');
  expect((global.fetch as jest.Mock).mock.calls.some(call=>call[1]?.method==='POST')).toBe(false);
 });
 it('lists available API-provided streams beyond the prepared traffic examples',async()=>{
  current.sources=recordedSources();
  current.sources.data.sources.push({id:'extra-camera',stream_id:'extra-camera',label:'Additional traffic camera',kind:'live',available:true});
  await setup();
  const select=screen.getByRole('combobox',{name:'Stream',exact:true});
  expect(within(select).getByRole('option',{name:'Additional traffic camera',exact:true})).toHaveValue('extra-camera');
  expect(within(select).getByRole('option',{name:'Additional traffic camera',exact:true})).toBeEnabled();
  fireEvent.change(select,{target:{value:'extra-camera'}});
  await act(async()=>{});
  expect(global.fetch).toHaveBeenCalledWith('/api/proxy/d1/demo/sources',expect.objectContaining({method:'POST',body:JSON.stringify({source_id:'extra-camera'})}));
 });
});
