// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import { stepTitleWithToolName } from '../lib-src/markdown/AgentThink';

describe('stepTitleWithToolName', () => {
  it('shows the named tool for legacy generic tool-call headings', () => {
    expect(stepTitleWithToolName('2 - Tool Call', 'Tool: vss_search\nArgs: {}')).toBe('2 - Tool Call: vss_search');
  });

  it('does not include normalized arguments or results in the tool heading', () => {
    expect(stepTitleWithToolName('2 - Tool Call', 'Tool: vss_search Args: {} Result: found')).toBe(
      '2 - Tool Call: vss_search',
    );
  });

  it('shows the named sub-agent for legacy generic sub-agent headings', () => {
    expect(stepTitleWithToolName('3 - Sub-Agent Call', 'Calling sub-agent: search_agent Args: {}')).toBe(
      '3 - Sub-Agent Call: search_agent',
    );
  });

  it('leaves already-specific and non-tool headings unchanged', () => {
    expect(stepTitleWithToolName('2 - Tool Call: vss_search', 'Tool: vss_search')).toBe('2 - Tool Call: vss_search');
    expect(stepTitleWithToolName('3 - Thought', 'Tool: vss_search')).toBe('3 - Thought');
  });
});
