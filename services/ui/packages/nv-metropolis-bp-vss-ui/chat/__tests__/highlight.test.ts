// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { CODE_HIGHLIGHT_MAX_CHARS, shouldHighlightCode } from '../lib-src/markdown/highlight';

describe('shouldHighlightCode', () => {
  it('colours a short JSON object', () => {
    expect(shouldHighlightCode('{\n  "query": "forklift"\n}')).toBe(true);
  });

  it('does not colour an empty block', () => {
    expect(shouldHighlightCode('')).toBe(false);
  });

  it('does not colour a payload over the cap', () => {
    expect(shouldHighlightCode(`{"blob":"${'x'.repeat(CODE_HIGHLIGHT_MAX_CHARS)}"}`)).toBe(false);
  });

  it('colours a block at the cap and not one character past it', () => {
    expect(shouldHighlightCode('n'.repeat(CODE_HIGHLIGHT_MAX_CHARS))).toBe(true);
    expect(shouldHighlightCode('n'.repeat(CODE_HIGHLIGHT_MAX_CHARS + 1))).toBe(false);
  });
});
