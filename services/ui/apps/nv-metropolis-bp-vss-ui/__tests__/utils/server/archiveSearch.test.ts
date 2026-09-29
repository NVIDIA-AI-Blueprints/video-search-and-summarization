// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/** @jest-environment node */
import {archiveSearchSummary, completedArchiveSearch, mayCompleteArchiveSearch, requiresFreshArchiveSearch} from '../../../utils/server/agentAdapter/archiveSearch';

const output = {data: [{sensor_id: 'actual-source', start_offset: 60, end_offset: 70, verification: {result: 'confirmed', criteria_met: {leaking: true}}}], search_messages: [], job_id: 'search-real'};
const receipt = {event: 'vss_job_completed', group: 'search', job_id: output.job_id, status: 'completed', exit_hint: 0};
const result = {command: '/usr/local/bin/vss search run embed --query liquid leaking from bottles --top-k 5', exitCode: 0, signal: null, timedOut: false, truncated: false, stdout: `${JSON.stringify(output)}\n${JSON.stringify(receipt)}\n`, stderr: ''};

describe('archive completion boundary', () => {
  it.each(['Find liquid leaking from bottles', 'liquid leaking from bottle', 'Find filling clips and show the top 3 results'])('allows a standalone search after its actual tool result: %s', question => {
    expect(mayCompleteArchiveSearch(question)).toBe(true);
  });
  it('keeps a recorded source context compatible with the Search route', () => {
    expect(mayCompleteArchiveSearch('[Context: [{"video_id":"recording-a","type":"video_file"}]]\n\nFind liquid leaking from bottles')).toBe(true);
  });
  it.each([
    '[Context: [{"mode":"live","scope":"selected_live_session","selected_bottles":[]}]]\n\nFind the latest overflow',
    '[Context: [{"mode":"live","selected_bottles":[{"track_id":"s:epoch-1:cycle-5"}]}]]\n\nWhat happened with this bottle?',
    '[Context: [{"analysis_kind":"bottle-cycles","stream_id":"recording-a","scene_id":"single-station","reference_level":0.72}]]\n\nWhich bottles overflowed?',
    'What happened with bottle cycle-305?',
    'Find leaking clips and compare the measured bottle fill levels.',
    'Find leaking clips, then show the live filling status.',
    'Search the archive and create an alert for that event.',
    'Find spills and also generate a report.',
    '[Context: malformed]\n\nFind clips',
  ])('preserves explicit inspection or multi-step work: %s', question => {
    expect(mayCompleteArchiveSearch(question)).toBe(false);
  });
  it('requires real successful Search CLI provenance and a matching job receipt', () => {
    const found = completedArchiveSearch({content: [{type: 'text', text: JSON.stringify(result)}]});
    expect(found?.data).toEqual([{...output.data[0], critic_result: output.data[0].verification}]);
    expect(found?.job_id).toBe(output.job_id);
    expect(archiveSearchSummary(found!)).toContain('1 clip candidate');
  });
  it.each([
    {...result, exitCode: 3}, {...result, timedOut: true}, {...result, truncated: true}, {...result, signal: 'SIGTERM'},
    {...result, command: '/usr/local/bin/vss filling live status'},
    {...result, stdout: JSON.stringify(output)},
    {...result, stdout: `${JSON.stringify(output)}\n${JSON.stringify({...receipt, job_id: 'other'})}`},
    {...result, stdout: `${JSON.stringify(output)}\n${JSON.stringify({...receipt, exit_hint: 3})}`},
    {...result, stdout: `${JSON.stringify({...output, data: ['invented']})}\n${JSON.stringify(receipt)}`},
    {data: output.data, search_messages: [], job_id: output.job_id},
    {...result,stdout:`<vss-ui-artifact>${JSON.stringify({version:'1.0',kind:'vss.search.results',payload:output})}</vss-ui-artifact>`},
  ])('does not terminate on unsuccessful, incomplete or unrelated output', value => {
    expect(completedArchiveSearch(value)).toBeUndefined();
  });
  it('reports an actual empty result without inventing an unavailable-recording cause or live status', () => {
    const empty = completedArchiveSearch({...result, stdout: `${JSON.stringify({...output, data: []})}\n${JSON.stringify(receipt)}`});
    expect(archiveSearchSummary(empty!)).toBe('Search returned no clip candidates for this query.');
  });
});


describe('fresh archive retrieval intent', () => {
  it.each([
    'Find liquid leaking from bottles', 'Show me liquid leaking from bottles',
    'Please find clips of liquid leaking from bottles', 'Can you search for a worker dropping a box?',
    'Search the archive for overflowing bottles', 'Look for a vehicle entering the warehouse',
    'Rerun that search', 'Retry the previous search', 'Repeat the last query', 'Run a fresh archive search using the exact query liquid leaking',
    '[Context: [{"video_id":"chosen-file","type":"video_file"}]]\n\nFind liquid leaking from bottles',
  ])('requires a current Search result: %s', message => expect(requiresFreshArchiveSearch(message)).toBe(true));
  it.each([
    'What videos are available?', 'Show me the available videos', 'Find all recordings',
    'Show me which videos are available', 'Show me previous search results',
    'What did the previous search find?', 'Why was that rejected?', 'Show me why that clip was rejected',
    'Show me that clip', 'Show me the first result', 'Show me the 60–70 second clip', 'Show me the search history', 'Find out why search is failing', 'Search the logs for errors',
    'Show me reports', 'Hello', 'What happened with bottle cycle-305?',
    'Find leaking clips and compare the measured bottle fill levels',
    'Find spills and then generate a report',
    '[Context: [{"mode":"live","scope":"selected_live_session","selected_bottles":[]}]]\n\nShow me overflowing bottles',
  ])('preserves non-retrieval or mixed conversation: %s', message => expect(requiresFreshArchiveSearch(message)).toBe(false));
});
