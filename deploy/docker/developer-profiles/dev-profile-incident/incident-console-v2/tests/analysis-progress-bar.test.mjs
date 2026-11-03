// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  conversionNote,
  formatElapsed,
  getStageAnnouncement,
  getStageProgress,
  stageLabels,
  stageOrder,
} from '../src/sections/analysis/stage-progress.ts';

test('analysis stages list has exactly 4 stages with proper sequential order and labels', () => {
  assert.equal(stageLabels.length, 4);
  assert.deepEqual(
    stageLabels.map((s) => s.key),
    ['uploading', 'analyzing', 'saving', 'complete'],
  );
  assert.equal(stageLabels[0].label, 'Uploading videos');
  assert.equal(stageOrder.uploading, 0);
  assert.equal(stageOrder.converting, 0, 'converting is part of the Uploading videos step');
  assert.equal(stageOrder.analyzing, 1);
  assert.equal(stageOrder.saving, 2);
  assert.equal(stageOrder.complete, 3);
});

test('getStageProgress computes smooth and accurate progress across stages', () => {
  assert.equal(getStageProgress('idle'), 0);
  assert.equal(getStageProgress('uploading', 0), 2);
  assert.equal(getStageProgress('uploading', 50), 8);
  assert.equal(getStageProgress('uploading', 100), 15);
  assert.equal(getStageProgress('converting'), 20);
  assert.equal(getStageProgress('analyzing'), 50);
  assert.equal(getStageProgress('saving'), 75);
  assert.equal(getStageProgress('complete'), 100);

  // When in error state, progress retains the failed stage progress
  assert.equal(getStageProgress('error', 0, 'analyzing'), 50);
  assert.equal(getStageProgress('error', 0, 'uploading'), 2);
  assert.equal(getStageProgress('error', 0, 'idle'), 0);
});

test('getStageAnnouncement generates clear accessible announcements for screen readers', () => {
  assert.match(getStageAnnouncement('uploading', 45), /Stage 1 of 4: Uploading videos \(45%\)\./);
  assert.equal(getStageAnnouncement('converting', 0, null, 83), 'Stage 1 of 4: Uploading videos, converting to H.264 (1:23).');
  assert.equal(getStageAnnouncement('analyzing'), 'Stage 2 of 4: Understanding the incident.');
  assert.equal(getStageAnnouncement('saving'), 'Stage 3 of 4: Structuring and saving report.');
  assert.equal(getStageAnnouncement('complete'), 'Stage 4 of 4: Report ready. Analysis complete.');
  assert.equal(getStageAnnouncement('error', 0, 'Gateway timed out'), 'Analysis failed: Gateway timed out');
});

test('the Uploading videos step shows elapsed time and says whether the file was converted', () => {
  assert.equal(formatElapsed(0), '0:00');
  assert.equal(formatElapsed(5.9), '0:05');
  assert.equal(formatElapsed(83), '1:23');
  assert.equal(formatElapsed(-3), '0:00');
  assert.equal(conversionNote(true, 83), 'Converted to H.264 MP4 at the original resolution in 1:23.');
  assert.equal(conversionNote(false, 0), 'Already an H.264 MP4: stored without conversion.');
});

test('AnalysisWorkspace component uses shadcn Progress and accessible ARIA attributes', async () => {
  const filePath = new URL('../src/sections/analysis/components/AnalysisWorkspace/AnalysisWorkspace.tsx', import.meta.url);
  const source = await readFile(filePath, 'utf8');

  // Verify shadcn Progress import and usage
  assert.match(source, /import\s+\{\s*Progress\s*\}\s+from\s+'@\/components\/ui\/progress'/);
  assert.match(source, /<Progress\b/);
  assert.match(source, /aria-label="Analysis progress"/);
  assert.match(source, /aria-valuenow=\{progressValue\}/);
  assert.match(source, /aria-valuetext=\{announcement\}/);

  // Verify screen reader live region announcement
  assert.match(source, /role="status"/);
  assert.match(source, /aria-live="polite"/);

  // Verify stage list beneath the progress bar
  assert.match(source, /aria-label="Analysis stages"/);
  assert.match(source, /aria-current=\{active \? 'step' : undefined\}/);
  assert.match(source, /data-testid="converting-elapsed"/, 'the Uploading videos step shows its elapsed time');
  assert.match(source, /\(completed\)/);
  assert.match(source, /\(current step\)/);
});
