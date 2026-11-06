// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const components = new URL('../src/sections/', import.meta.url);

test('report page shows structured fields without run and model-output details', async () => {
  const report = (
    await Promise.all(
      ['reports/components/ReportPane/ReportPane.tsx', 'reports/components/ReportPage/ReportPage.tsx'].map((name) =>
        readFile(new URL(name, components), 'utf8'),
      ),
    )
  ).join('\n');

  assert.match(report, /Severity rationale/);
  assert.match(report, /reviewer set/, 'an edited severity is shown against the model severity');
  assert.doesNotMatch(report, /Model event timeline/);
  assert.doesNotMatch(report, /Model-reported uncertainties/);
  assert.doesNotMatch(report, /timeline-inconsistent/);
  assert.doesNotMatch(report, /Run &amp; model output details/);
  assert.match(report, /Contract repair applied \(IDs only\)/);
  assert.match(report, /confidenceScore !== null/, 'no confidence percentage when the model returned none');
  assert.doesNotMatch(report, /createdBy/, 'human versions show no author name');
  assert.match(report, /ReportEditPane/, 'editing opens the side-by-side pane');
  assert.match(report, /Edit from v/, 'the report opens edit mode from the shown version');
  assert.match(report, /Model classified this as/);
  assert.match(report, /model window/);
});

test('the report page has a History of versions, a reviewer prompt and explicit official controls, and no verification flow', async () => {
  const report = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  assert.match(report, /ReportVersions/);
  const history = await readFile(new URL('reports/components/ReportVersions/ReportVersions.tsx', components), 'utf8');
  assert.doesNotMatch(history, /Analysis history/);
  assert.match(history, /markOfficial/);
  assert.match(history, /role="alert"/, 'the agent-unreachable message is shown inline');
  assert.doesNotMatch(history, /restore/i);
  const reanalysis = await readFile(new URL('analysis/components/ReanalysisCard/ReanalysisCard.tsx', components), 'utf8');
  assert.match(reanalysis, /Reviewer prompt \(optional\)/);
  assert.match(reanalysis, /additionalInstruction/);
  for (const name of [
    'reports/components/ReportPage/ReportPage.tsx',
    'reports/components/ReportPane/ReportPane.tsx',
    'reports/components/ReportEditPane/ReportEditPane.tsx',
    'reports/components/ReportVersions/ReportVersions.tsx',
    'reports/components/OfficialControls/OfficialControls.tsx',
    'library/components/LibraryScreen/LibraryScreen.tsx',
    'library/components/IncidentCard/IncidentCard.tsx',
  ]) {
    const source = await readFile(new URL(name, components), 'utf8');
    assert.doesNotMatch(
      source,
      /Unverify|under review|api\/reports\/[^'`]*\/review|review_status|reviewStatus/,
      `${name} has no verification flow`,
    );
  }
});

test('library incident cards show no confidence percentage', async () => {
  const card = await readFile(new URL('library/components/IncidentCard/IncidentCard.tsx', components), 'utf8');
  assert.doesNotMatch(card, /confidence/);
});

test('browser components import only browser-safe report modules', async () => {
  const serverOnly = [
    /@\/lib\/reports\/view'/,
    /@\/lib\/reports\/run-notes'/,
    /@\/lib\/contract\/(load|validate)'/,
    /@\/lib\/analysis\/(persistence|run-contract-analysis|build-request)'/,
    /from 'node:/,
  ];
  const clientOnly = await readFile(new URL('../src/modules/reports/domain/report-view.ts', import.meta.url), 'utf8');
  for (const name of [
    'reports/components/ReportPage/ReportPage.tsx',
    'reports/components/ReportPane/ReportPane.tsx',
    'reports/components/ReportVersions/ReportVersions.tsx',
    'reports/components/ReportRunDetails/ReportRunDetails.tsx',
    'reports/components/ReportEditPane/ReportEditPane.tsx',
    'analysis/components/ReanalysisCard/ReanalysisCard.tsx',
    'reports/components/RunComparison/RunComparison.tsx',
    'analysis/components/AnalysisWorkspace/AnalysisWorkspace.tsx',
    'library/components/LibraryScreen/LibraryScreen.tsx',
    'library/components/IncidentCard/IncidentCard.tsx',
  ]) {
    const source = await readFile(new URL(name, components), 'utf8');
    for (const pattern of serverOnly) assert.doesNotMatch(source, pattern, `${name} must not import ${pattern}`);
  }
  for (const line of clientOnly.split('\n').filter((l) => l.startsWith('import '))) {
    assert.match(line, /^import type /, `src/modules/reports/domain/report-view.ts may only import types: ${line}`);
  }
});

test('edit mode pairs every evidence row with A and saves the whole draft as a new version', async () => {
  const editor = await readFile(new URL('reports/components/ReportEditPane/ReportEditPane.tsx', components), 'utf8');
  assert.match(editor, /pairReport/);
  assert.match(editor, /ReadCard/);
  assert.match(editor, /EditCard/);
  assert.match(editor, /New in B/);
  assert.match(editor, /Deleted/);
  assert.match(editor, /Revert to v/);
  assert.match(editor, /Delete/);
  assert.doesNotMatch(editor, /createdBy/, 'the pane never shows author names');

  const page = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  assert.match(page, /draftFromView/);
  assert.match(page, /editFromDraft/);
  assert.match(page, /draftProblems/);
  assert.match(page, /parentId: editBase\.revisionId/, 'the new version is parented on the version being edited');
  assert.match(page, /note\.trim\(\)/, 'a note is required before saving');
  assert.match(page, /Save as new version/);
  assert.match(page, />\s*Undo\s*</, 'the editor can undo the latest draft change');
  assert.match(page, />\s*Revert\s*</, 'the editor can restore the opened version');
  assert.match(page, /<ResizablePanelGroup/, 'the report page uses the shared resizable layout in both modes');
  assert.match(page, /Resize media workspace and report details/);
  assert.match(page, /Resize video and timeline/);
  assert.match(page, /Resize timeline and versions/);
  const timeline = await readFile(new URL('reports/components/ReportTimeline/ReportTimeline.tsx', components), 'utf8');
  assert.match(timeline, /one lane per entity and what they hold/);
  assert.match(timeline, /Entities/);
  assert.match(timeline, /Instruments/);
  assert.match(timeline, /Assets/);
  for (const classB of ['severity_reason', 'uncertainties', 'timeline']) {
    assert.ok(!new RegExp(`${classB}:`).test(editor), `${classB} must not be editable`);
  }
});

test('the report page offers re-analysis', async () => {
  const report = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  assert.match(report, /ReanalysisDialog/);
  const reanalysis = await readFile(new URL('analysis/components/ReanalysisCard/ReanalysisCard.tsx', components), 'utf8');
  assert.match(reanalysis, /ReanalysisDialog/);
  assert.match(reanalysis, /text-lg font-bold tracking-tight/, 'the re-analysis dialog has a prominent title');
  assert.doesNotMatch(reanalysis, /DialogHeader className="[^"]*border-l-/, 'the modal header has no left accent line');
  const library = await readFile(new URL('library/components/LibraryScreen/LibraryScreen.tsx', components), 'utf8');
  assert.doesNotMatch(library, /\/api\/analysis/, 'the library no longer re-runs with the default model');
  assert.match(await readFile(new URL('library/components/IncidentCard/IncidentCard.tsx', components), 'utf8'), /library-repair-marker/);
  const card = await readFile(new URL('library/components/IncidentCard/IncidentCard.tsx', components), 'utf8');
  assert.doesNotMatch(card, /\/videos\//, 'report cards do not link to the retired video-history page');
});

test('report chat uses the same title band as report sections', async () => {
  const chat = await readFile(new URL('reports/components/ReportChat/ReportChat.tsx', components), 'utf8');
  assert.doesNotMatch(chat, /border-l-primary/);
  assert.match(chat, /text-lg font-bold tracking-tight/);
  assert.match(chat, /<SheetClose asChild>/, 'the chat has an explicit close control');
  assert.match(chat, /className="absolute top-3 right-3"/, 'the close control sits at the top-right');
  assert.match(chat, /rounded-none/, 'the chat title surface is flush with the browser edge');
  assert.doesNotMatch(chat, /Asking about \{target\.label\}/, 'the version badge omits the redundant prefix');
  const page = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  assert.match(page, /showCloseButton=\{false\}/, 'the separately positioned default close button is disabled');
});

test('edit evidence pairs fit the evidence-card footprint', async () => {
  const edit = await readFile(new URL('reports/components/ReportEditPane/ReportEditPane.tsx', components), 'utf8');
  assert.match(edit, /w-\[min\(24rem,calc\(100vw-4rem\)\)\]/);
  assert.doesNotMatch(edit, /w-\[min\(42rem,calc\(100vw-4rem\)\)\]/);
  assert.doesNotMatch(edit, /CardHeader className="gap-0 border-b/, 'edit evidence cards do not add a separate title band');
  assert.match(edit, /max-w-88 gap-0 overflow-hidden/, 'the video and title content have no card-level gap');
  assert.match(edit, /<DraftVideoFrame/, 'a newly selected box is previewed from the browser-loaded video in its draft card');
  assert.match(edit, /testId="draft-evidence-video-preview"/);
  assert.match(edit, /data-testid="edit-readonly-evidence-id"/, 'the read-only A card has the same ID badge as View');
  assert.match(edit, /kind=\{kind\}/, 'the read-only A card has the same image kind badge as View');
});

test('no component renders stored timestamps with a bare new Date(...).toLocaleString()', async () => {
  for (const name of [
    'reports/components/ReportPane/ReportPane.tsx',
    'reports/components/ReportEditPane/ReportEditPane.tsx',
    'reports/components/ReportVersions/ReportVersions.tsx',
    'reports/components/RunComparison/RunComparison.tsx',
    'library/components/LibraryScreen/LibraryScreen.tsx',
    'library/components/IncidentCard/IncidentCard.tsx',
    'library/components/HitCard/HitCard.tsx',
  ]) {
    const source = await readFile(new URL(name, components), 'utf8');
    assert.doesNotMatch(source, /new Date\([^)]*\)\.toLocale/, name);
  }
});

test('the new-analysis model selector shows full model names and never clips them', async () => {
  const workspace = await readFile(new URL('analysis/components/AnalysisWorkspace/AnalysisWorkspace.tsx', components), 'utf8');
  const select =
    /<SelectTrigger\s+className="([^"]*)"[^>]*id="analysis-model"/.exec(workspace) ||
    /<select\s+className="([^"]*)"[^>]*id="analysis-model"/.exec(workspace);
  assert.ok(select, 'the model selector exists');
  const classes = select[1].split(/\s+/);
  for (const needed of ['sm:w-auto', 'sm:min-w-[19rem]', 'sm:shrink-0']) assert.ok(classes.includes(needed), `select has ${needed}`);
  assert.ok(!classes.some((name) => /max-w-/.test(name)), 'no max width that would clip a model name');
  assert.match(
    workspace,
    /<(?:option|SelectItem) key=\{option\.id\} value=\{option\.id\}>\s*\{option\.label\}\s*<\/(?:option|SelectItem)>/,
    'options show the full friendly label and keep the model id as value',
  );
  assert.match(workspace, /sm:flex-wrap/, 'on narrow rows the note wraps instead of squeezing the selector');

  const inference = JSON.parse(await readFile(new URL('../../contracts/inference.json', import.meta.url), 'utf8'));
  assert.deepEqual(
    inference.models.map((model) => model.label),
    ['Cosmos 3 Nano Reasoner', 'Cosmos 3 Super Reasoner', 'Nemotron 3 Nano Omni Reasoning'],
  );
});
