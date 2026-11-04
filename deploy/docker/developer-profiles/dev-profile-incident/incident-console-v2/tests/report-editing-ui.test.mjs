// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const components = new URL('../src/sections/', import.meta.url);

test('report page separates structured fields, read-only model output and run details', async () => {
  const report = (
    await Promise.all(
      [
        'reports/components/ReportPane/ReportPane.tsx',
        'reports/components/ReportRunDetails/ReportRunDetails.tsx',
        'reports/components/ReportPage/ReportPage.tsx',
      ].map((name) => readFile(new URL(name, components), 'utf8')),
    )
  ).join('\n');

  assert.match(report, /Severity rationale/);
  assert.match(report, /reviewer set/, 'an edited severity is shown against the model severity');
  assert.doesNotMatch(report, /Model event timeline/);
  assert.doesNotMatch(report, /Model-reported uncertainties/);
  assert.doesNotMatch(report, /timeline-inconsistent/);
  assert.match(report, /Run &amp; model output details/);
  assert.match(report, /Additional instruction/);
  assert.match(report, /Prompt SHA-256/);
  assert.match(report, /Contract repair applied \(IDs only\)/);
  assert.match(report, /Repair operations/);
  assert.match(report, /First-pass violations/);
  assert.match(report, /Valid after structural repair/);
  assert.match(report, /confidenceScore !== null/, 'no confidence percentage when the model returned none');
  assert.doesNotMatch(report, /createdBy/, 'human versions show no author name');
  assert.match(report, /ReportEditPane/, 'editing opens the side-by-side pane');
  assert.match(report, /Edit from v/, 'the report opens edit mode from the shown version');
  assert.match(report, /Model classified this as/);
  assert.match(report, /model window/);
  assert.match(report, /Validation policy/);
  assert.match(report, /Full contract validity \(first response\)/);
});

test('the report page has a History of versions, a reviewer prompt and explicit official controls, and no verification flow', async () => {
  const report = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  assert.match(report, /ReportVersions/);
  const history = await readFile(new URL('reports/components/ReportVersions/ReportVersions.tsx', components), 'utf8');
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
    'analysis/components/RunHistory/RunHistory.tsx',
    'analysis/components/ReanalysisCard/ReanalysisCard.tsx',
    'videos/components/VideoHistoryScreen/VideoHistoryScreen.tsx',
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
  assert.match(page, /Resize timeline and analysis history/);
  for (const classB of ['severity_reason', 'uncertainties', 'timeline']) {
    assert.ok(!new RegExp(`${classB}:`).test(editor), `${classB} must not be editable`);
  }
});

test('the report page offers re-analysis', async () => {
  const report = await readFile(new URL('reports/components/ReportPage/ReportPage.tsx', components), 'utf8');
  assert.match(report, /ReanalysisDialog/);
  const reanalysis = await readFile(new URL('analysis/components/ReanalysisCard/ReanalysisCard.tsx', components), 'utf8');
  assert.match(reanalysis, /ReanalysisDialog/);
  const library = await readFile(new URL('library/components/LibraryScreen/LibraryScreen.tsx', components), 'utf8');
  assert.doesNotMatch(library, /\/api\/analysis/, 'the library no longer re-runs with the default model');
  assert.match(await readFile(new URL('library/components/IncidentCard/IncidentCard.tsx', components), 'utf8'), /library-repair-marker/);
  const history = await readFile(new URL('analysis/components/RunHistory/RunHistory.tsx', components), 'utf8');
  assert.match(history, /no report/);
  assert.match(history, /Contract violations/);
});

test('no component renders stored timestamps with a bare new Date(...).toLocaleString()', async () => {
  for (const name of [
    'reports/components/ReportPane/ReportPane.tsx',
    'reports/components/ReportEditPane/ReportEditPane.tsx',
    'reports/components/ReportVersions/ReportVersions.tsx',
    'reports/components/RunComparison/RunComparison.tsx',
    'analysis/components/RunHistory/RunHistory.tsx',
    'videos/components/VideoHistoryScreen/VideoHistoryScreen.tsx',
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
