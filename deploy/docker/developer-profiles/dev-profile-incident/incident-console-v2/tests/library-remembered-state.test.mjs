// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { LIBRARY_STATE_KEY, forgetLibraryState } from '../src/lib/library/remembered-state.ts';

test('forgetting the remembered state removes the page and filters saved when a report was opened', () => {
  const store = new Map([[LIBRARY_STATE_KEY, JSON.stringify({ filters: { page: 2 } })]]);
  forgetLibraryState({ removeItem: (key) => store.delete(key) });
  assert.equal(store.has(LIBRARY_STATE_KEY), false);
});

test('unavailable storage does not break navigation', () => {
  forgetLibraryState({
    removeItem: () => {
      throw new Error('blocked');
    },
  });
  forgetLibraryState(undefined);
});

test('the Library tab clears the remembered state while the screen still uses the same key for Back', async () => {
  const header = await readFile(new URL('../src/sections/shell/components/AppHeader/AppHeader.tsx', import.meta.url), 'utf8');
  const screen = await readFile(new URL('../src/sections/library/components/LibraryScreen/LibraryScreen.tsx', import.meta.url), 'utf8');
  assert.equal(header.match(/forgetLibraryState\(\)/g)?.length, 2, 'desktop and mobile Library links both clear it');
  assert.match(screen, /STATE_KEY = LIBRARY_STATE_KEY/);
  assert.match(screen, /rememberPosition/);
});
