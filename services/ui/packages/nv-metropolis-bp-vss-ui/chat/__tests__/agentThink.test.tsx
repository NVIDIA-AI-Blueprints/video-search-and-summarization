// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import { stepTitleWithToolName } from '../lib-src/markdown/AgentThink';

describe('stepTitleWithToolName', () => {
  it('shows the named tool for legacy generic tool-call headings', () => {
    expect(stepTitleWithToolName('2 - Tool Call', 'Tool: vss_search\nArgs: {}')).toBe('2 - Tool Call: vss_search');
  });

  it('leaves already-specific and non-tool headings unchanged', () => {
    expect(stepTitleWithToolName('2 - Tool Call: vss_search', 'Tool: vss_search')).toBe('2 - Tool Call: vss_search');
    expect(stepTitleWithToolName('3 - Thought', 'Tool: vss_search')).toBe('3 - Thought');
  });
});
