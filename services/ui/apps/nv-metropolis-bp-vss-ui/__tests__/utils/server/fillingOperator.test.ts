// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import {fillingOperatorText, fillingRequestScope} from '../../../utils/server/agentAdapter/fillingOperator';

const scope = {session: 'session', stream: 'camera', selected: ['session:epoch-1:cycle-1', 'session:epoch-1:cycle-2']};
const result = {mode: 'live', view: 'operator', render_policy: 'authoritative_measurement', session_id: 'session', stream_id: 'camera', query_status: 'ok', selected_track_ids: scope.selected,
  matches: [{event_id:'one',track_id:scope.selected[0], status:'normal',final_level:.73}, {event_id:'two',track_id:scope.selected[1],status:'underfill',final_level:.29}],
  snapshot_evidences: [], display_markdown:'Bottle cycle-1: normal. Height 73%. Bottle cycle-2: underfill. Height 29%.'};

test('only recognized operator output renders the actual measured answer', () => {
  const tool = {content:[{type:'text',text:JSON.stringify({exitCode:0,stdout:JSON.stringify(result)})}]};
  expect(fillingOperatorText(tool, scope)).toBe(result.display_markdown);
  expect(fillingOperatorText({text:'An ordinary model reply'}, scope)).toBeUndefined();
  expect(fillingOperatorText({exitCode:1,stdout:JSON.stringify(result)}, scope)).toBeUndefined();
});
test('wrong bottle evidence or source fails instead of painting it', () => {
  expect(() => fillingOperatorText({...result,stream_id:'other'}, scope)).toThrow();
  expect(() => fillingOperatorText({...result,selected_track_ids:['wrong']}, scope)).toThrow();
  expect(() => fillingOperatorText({...result,snapshot_evidences:[{session_id:'session',stream_id:'camera',event_id:'unrelated',verified:true,verification:'exact-decoded-frame'}]}, scope)).toThrow();
});
test('provisional and unavailable results retain their actual explanation', () => {
  expect(fillingOperatorText({...result,query_status:'in_progress',matches:[],display_markdown:'Selected bottle is still being observed; no final verdict.'},scope)).toContain('no final verdict');
  expect(() => fillingOperatorText({...result,display_markdown:''},scope)).toThrow();
});
test('explicit typed cycle labels override actual ambient selection', () => {
  const context = {mode:'live',scope:'selected_live_session',session_id:'session',stream_id:'camera',selected_bottles:scope.selected.map(track_id=>({track_id}))};
  const prefix = `[Context: ${JSON.stringify([context])}]\n\n`;
  expect(fillingRequestScope(prefix+'Compare these two bottles.')?.selected).toEqual(scope.selected);
  expect(fillingRequestScope(prefix+'What happened with cycle-99?')?.selected).toEqual([]);
  expect(fillingRequestScope(prefix+'Find clips in the recorded video.')).toBeUndefined();
});


test('explicit question IDs bind the returned tool records even when ambient selection is ignored', () => {
  const explicitScope = {...scope, selected:[], explicit:['cycle-9']};
  expect(() => fillingOperatorText({...result,requested_cycle_ids:['cycle-1','cycle-2']},explicitScope)).toThrow('explicitly requested');
  expect(() => fillingOperatorText({...result,requested_cycle_ids:['cycle-9']},explicitScope)).toThrow('different explicit');
  expect(fillingOperatorText({...result,requested_cycle_ids:['cycle-9'],matches:[],query_status:'not_found',display_markdown:'cycle-9 was not found.'},explicitScope)).toBe('cycle-9 was not found.');
  expect(fillingOperatorText({...result,requested_cycle_ids:['cycle-9'],matches:[],query_status:'ambiguous',candidates:[{track_id:'session:epoch-1:cycle-9'},{track_id:'session:epoch-2:cycle-9'}],display_markdown:'Select the epoch for cycle-9.'},explicitScope)).toBe('Select the epoch for cycle-9.');
});


test('a vague unselected question cannot be answered by guessing an actual current bottle', () => {
  expect(() => fillingOperatorText(result,{...scope,selected:[],selectionRequired:1})).toThrow('Select the requested');
  expect(fillingOperatorText({...result,query_status:'unsupported',matches:[],requested_cycle_ids:[],display_markdown:'Select one bottle first.'},{...scope,selected:[],selectionRequired:1})).toBe('Select one bottle first.');
});


test('explicit padded cycle labels retain the correct identity and reject another bottle', () => {
  const context = {mode:'live',scope:'selected_live_session',session_id:'session',stream_id:'camera',selected_bottles:scope.selected.map(track_id=>({track_id}))};
  const explicitScope = fillingRequestScope(`[Context: ${JSON.stringify([context])}]\n\nWhat happened with bottle cycle-03?`)!;
  expect(explicitScope.explicit).toEqual(['cycle-3']);
  const padded = {...result,selected_track_ids:[],requested_cycle_ids:['cycle-3'],matches:[{event_id:'three',track_id:'session:epoch-1:cycle-03',status:'normal'}],display_markdown:'Bottle cycle-3: normal.'};
  expect(fillingOperatorText(padded,explicitScope)).toBe(padded.display_markdown);
  expect(() => fillingOperatorText({...padded,matches:[{event_id:'four',track_id:'session:epoch-1:cycle-04',status:'underfill'}]},explicitScope)).toThrow('different explicit');
  // Selection binds an opaque full track identity, not a canonicalized short alias.
  expect(() => fillingOperatorText({...padded,selected_track_ids:['session:epoch-1:cycle-3']},{...scope,selected:['session:epoch-1:cycle-03']})).toThrow('changed the selected');
});
