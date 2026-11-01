// SPDX-License-Identifier: Apache-2.0

// The library chat: a question names no incident or version (library scope), citations are grouped by incident
// and open that incident's report at the cited second, and the panel is mounted on the library page.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { register } from 'node:module';
import { join } from 'node:path';
import { test } from 'node:test';
import Ajv2020 from 'ajv/dist/2020.js';

register('./support/alias-loader.mjs', import.meta.url);

const { libraryChatRequestBody, citationsByIncident, citationHref, citationLabel, matchingReports } = await import(
  '../src/lib/chat/library-chat.ts'
);

const ajv = new Ajv2020({ allErrors: true, strict: false });
const validateRequest = ajv.compile(
  JSON.parse(readFileSync(join(process.cwd(), '..', 'contracts', 'chat-contract-v1', 'chat_request.schema.json'), 'utf8')),
);

const citation = (incidentId, itemId, timestampSeconds) => ({
  incidentId,
  itemId,
  itemType: 'instrument',
  timestampSeconds,
  cropImageUrl: `https://r2.example/${incidentId}/${itemId}.jpg`,
});

test('a library question carries no incident, version or draft, so the agent answers in library scope', () => {
  const body = libraryChatRequestBody({ messages: [{ role: 'user', content: 'How many knives?' }] });
  assert.deepEqual(body, { messages: [{ role: 'user', content: 'How many knives?' }] });
  const next = libraryChatRequestBody({ messages: [{ role: 'user', content: 'And burglaries?' }], sessionId: 's-1' });
  assert.equal(next.sessionId, 's-1');
  for (const key of ['incidentId', 'versionId', 'draftReport', 'context']) assert.equal(key in next, false, key);
});

test('a library request is a valid chat request once the route maps it', async () => {
  const { toAgentChatBody } = await import('../src/modules/chat/infrastructure/agent/agent-chat.ts');
  const agentBody = toAgentChatBody(libraryChatRequestBody({ messages: [{ role: 'user', content: 'Which incidents?' }] }));
  assert.ok(validateRequest(agentBody), JSON.stringify(validateRequest.errors));
  assert.equal(agentBody.version_id, undefined);
});

test('citations are grouped by incident in the order the answer cites them', () => {
  const groups = citationsByIncident([citation('Assault001', 'I1', 4), citation('Burglary002', 'A1', 9), citation('Assault001', 'E2', 6)]);
  assert.deepEqual(
    groups.map((group) => [group.incidentId, group.citations.map((c) => c.itemId)]),
    [
      ['Assault001', ['I1', 'E2']],
      ['Burglary002', ['A1']],
    ],
  );
  assert.deepEqual(citationsByIncident([]), []);
});

test("a citation opens its incident's report at the version read and the cited second", () => {
  assert.equal(citationHref(citation('Assault001', 'I1', 12.7)), '/reports/Assault001?t=12');
  assert.equal(citationHref(citation('Assault001', 'I1', 12.7), 31), '/reports/Assault001?revision=31&t=12');
  assert.equal(citationHref(citation('Assault001', 'I1', 0), 31), '/reports/Assault001?revision=31');
  assert.equal(citationHref(citation('Assault001', 'I1', 0)), '/reports/Assault001');
  assert.equal(citationHref(citation('Assault001', 'I1', 4), 0), '/reports/Assault001?t=4', 'no version id 0');
  assert.equal(citationHref(citation('a/b c', 'I1', 3)), '/reports/a%2Fb%20c?t=3');
});

test('a citation is labelled by the name the answer gave the item, else its kind, never its id', () => {
  const names = { 'Assault001/I1': 'the glass bottle' };
  assert.equal(citationLabel(citation('Assault001', 'I1', 4), names), 'The glass bottle');
  assert.equal(citationLabel(citation('Burglary002', 'I1', 4), names), 'Instrument', 'the same id in another incident');
  assert.equal(citationLabel(citation('Assault001', 'I1', 4)), 'Instrument');
});

test('a code-composed count links its matching reports at their versions; a refusal or a cited answer does not', () => {
  const grounding = { candidates: ['V1', 'V3'], versions: { V1: 11, V3: 31 }, answeredBy: 'code' };
  assert.deepEqual(matchingReports({ citations: [] }, grounding), [
    { incidentId: 'V1', href: '/reports/V1?revision=11' },
    { incidentId: 'V3', href: '/reports/V3?revision=31' },
  ]);
  assert.deepEqual(matchingReports({ citations: [], insufficientEvidence: true }, grounding), []);
  assert.deepEqual(matchingReports({ citations: [citation('V1', 'I1', 4)] }, grounding), []);
  assert.deepEqual(matchingReports({ citations: [] }, { ...grounding, answeredBy: 'model' }), []);
  assert.deepEqual(matchingReports({ citations: [] }, undefined), []);
});

test('the library page mounts the chat in a titled sheet', () => {
  const screen = readFileSync(
    join(process.cwd(), 'src', 'sections', 'library', 'components', 'LibraryScreen', 'LibraryScreen.tsx'),
    'utf8',
  );
  assert.match(screen, /<SheetTitle>Ask the library<\/SheetTitle>/);
  assert.match(screen, /<LibraryChat \/>/);
  const panel = readFileSync(join(process.cwd(), 'src', 'sections', 'library', 'components', 'LibraryChat', 'LibraryChat.tsx'), 'utf8');
  assert.match(panel, /libraryChatRequestBody/);
  assert.doesNotMatch(panel, /incidentId:/, 'the panel never names an incident in its request');
  assert.doesNotMatch(panel, />\s*\{citation\.itemId\}/, 'a citation chip never shows an item id (it is only a React key)');
  assert.match(panel, /citationLabel\(citation, message\.grounding\?\.itemNames\)/);
});
