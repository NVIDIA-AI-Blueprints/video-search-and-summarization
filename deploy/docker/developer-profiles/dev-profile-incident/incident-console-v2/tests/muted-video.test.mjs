// SPDX-License-Identifier: Apache-2.0

// Every video in the console is silent: no source file renders a raw <video>, so all go through MutedVideo.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

const SRC = new URL('../src', import.meta.url).pathname;

function sources(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? sources(join(dir, entry.name)) : /\.tsx$/.test(entry.name) ? [join(dir, entry.name)] : [],
  );
}

test('only MutedVideo renders a <video>', () => {
  const raw = sources(SRC).filter((file) => !file.endsWith('components/muted-video.tsx') && /<video[\s>]/.test(readFileSync(file, 'utf8')));
  assert.deepEqual(raw, []);
});

test('MutedVideo mutes, zeroes the volume and re-mutes on volume changes', () => {
  const source = readFileSync(join(SRC, 'components/muted-video.tsx'), 'utf8');
  assert.match(source, /\bmuted\b\n/);
  assert.match(source, /video\.volume = 0/);
  assert.match(source, /onVolumeChange/);
});

test('the overlay of the Ask about this incident sheet does not blur the page', () => {
  assert.doesNotMatch(readFileSync(join(SRC, 'components/ui/sheet.tsx'), 'utf8'), /backdrop-blur/);
});
