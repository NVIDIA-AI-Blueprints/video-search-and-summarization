// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';
import Ajv2020 from 'ajv/dist/2020.js';

register('./support/alias-loader.mjs', import.meta.url);

const { chatTarget, draftReport, chatRequestBody } = await import('../src/lib/chat/report-chat.ts');
const { toAgentChatBody } = await import('../src/modules/chat/infrastructure/agent/agent-chat.ts');

const ajv = new Ajv2020({ allErrors: true, strict: false });
const validateRequest = ajv.compile(
  JSON.parse(readFileSync(join(process.cwd(), '..', 'contracts', 'chat-contract-v1', 'chat_request.schema.json'), 'utf8')),
);

const details = (key, sourceId, extra = {}) => ({
  key,
  sourceId,
  timestampSeconds: 12,
  label: 'label',
  image: null,
  imageUrl: null,
  ...extra,
});
const draft = () => ({
  incident: { type: 'burglary', start_timestamp: 5, end_timestamp: 40, description: 'A break-in', severity_level: 3 },
  entities: [
    { ...details('entity-0', 'E1'), type: 'human', description: 'Guard' },
    { ...details('entity-new', null), type: 'unknown', description: 'Added' },
  ],
  instruments: [
    { ...details('instrument-0', 'I1'), holderKey: 'entity-new', name: 'Torch', description: '', threat_level: null },
    { ...details('instrument-1', null, { timestampSeconds: null }), holderKey: null, name: 'Bar', description: '', threat_level: 4 },
  ],
  assets: [{ ...details('asset-0', 'A1'), name: 'Van', description: '' }],
});
const revision = { revisionId: 42, revisionNo: 2, kind: 'human' };

test('View asks about the version on screen and says whether it is official', () => {
  assert.deepEqual(chatTarget({ revision, isOfficial: false, draft: null }), {
    versionId: 42,
    draftReport: null,
    label: 'v2 · Human · not official',
  });
  assert.equal(chatTarget({ revision: { ...revision, kind: 'ai' }, isOfficial: true, draft: null }).label, 'v2 · AI · official');
});

test('Edit asks about the draft, edited from the version being edited', () => {
  const target = chatTarget({ revision, isOfficial: true, draft: draft() });
  assert.equal(target.versionId, 42);
  assert.equal(target.label, 'Draft from v2 · unsaved');
  assert.ok(target.draftReport);
});

test('the draft report keeps source ids, resolves holders and leaves half-filled values out', () => {
  const report = draftReport(draft());
  assert.deepEqual(
    report.entities.map((entity) => entity.entity_id),
    ['E1', 'new-E2'],
  );
  assert.deepEqual(
    report.instruments.map((instrument) => [instrument.instrument_id, instrument.entity_id]),
    [
      ['I1', 'new-E2'],
      ['new-I2', null],
    ],
  );
  assert.equal('threat_level' in report.instruments[0], false, 'an unrated threat is left out');
  assert.equal('timestamp_seconds' in report.instruments[1], false, 'an unset time is left out');
  assert.deepEqual(
    report.assets.map((asset) => asset.asset_id),
    ['A1'],
  );
  assert.equal(report.incident.severity_level, 3);
  assert.deepEqual(
    draftReport({ ...draft(), incident: { type: null, start_timestamp: null, end_timestamp: null, description: '', severity_level: null } })
      .incident,
    { description: '' },
  );
});

test('the request carries the incident, the version and (in Edit) the draft, as a valid chat-contract-v1 body', () => {
  const messages = [{ role: 'user', content: 'Who is there?' }];
  const view = chatRequestBody({ messages, videoId: 'Burglary01', target: chatTarget({ revision, isOfficial: false, draft: null }) });
  assert.deepEqual(view, { messages, incidentId: 'Burglary01', versionId: 42 });

  const edit = chatRequestBody({
    messages,
    sessionId: 's1',
    videoId: 'Burglary01',
    target: chatTarget({ revision, isOfficial: false, draft: draft() }),
  });
  assert.equal(edit.versionId, 42);
  const agentBody = toAgentChatBody({
    messages: edit.messages,
    sessionId: edit.sessionId,
    context: { scope: 'incident', incidentId: edit.incidentId },
    versionId: edit.versionId,
    draftReport: edit.draftReport,
  });
  assert.equal(validateRequest(agentBody), true, JSON.stringify(validateRequest.errors));
  assert.equal(agentBody.version_id, 42);
  assert.equal(agentBody.draft_report.entities.length, 2);
});

test('a citation under an answer is named like its evidence card, never by its id', async () => {
  const { citationTitles, citationTitle } = await import('../src/lib/chat/report-chat.ts');
  const titles = citationTitles({
    entities: [
      { entityId: 'E1', type: 'human', label: 'person in light shirt' },
      { entityId: 'E2', type: 'human', label: '' },
    ],
    instruments: [{ instrumentId: 'I1', name: 'cash register', label: 'black bag' }],
    assets: [{ assetId: 'A1', name: '', label: 'stack of papers' }],
  });
  assert.deepEqual(titles, { E1: 'Person in light shirt', E2: 'Human', I1: 'Cash register', A1: 'Stack of papers' });
  assert.deepEqual(citationTitles(null), {});
  assert.equal(citationTitle(titles, { itemId: 'I1', itemType: 'instrument' }), 'Cash register');
  assert.equal(citationTitle(titles, { itemId: 'new-E3', itemType: 'entity' }), 'Entity', 'an item added in a draft');
  const chat = readFileSync(join(process.cwd(), 'src', 'sections', 'reports', 'components', 'ReportChat', 'ReportChat.tsx'), 'utf8');
  assert.doesNotMatch(chat, />\s*\{citation\.itemId\}/, 'the chip never renders the id');
  assert.match(chat, /citationTitle\(titles, citation\)/);
});
