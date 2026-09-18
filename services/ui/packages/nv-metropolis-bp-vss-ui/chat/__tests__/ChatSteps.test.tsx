// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

import { ChatSteps } from '../lib-src/ChatSteps';
import type { ChatStep } from '../lib-src/types';

const step = (
  id: string,
  status: ChatStep['status'] = 'complete',
  extras: Partial<ChatStep> = {},
): ChatStep => ({
  id,
  name: id,
  status,
  index: 0,
  ...extras,
});

describe('ChatSteps spinner', () => {
  it('spins on the header while the turn is still streaming', () => {
    render(<ChatSteps steps={[step('search', 'in_progress')]} streaming />);
    expect(screen.getByRole('status', { name: 'Intermediate steps running' })).toBeInTheDocument();
  });

  it('drops the header spinner once the answer lands', () => {
    render(<ChatSteps steps={[step('search', 'complete')]} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('stops spinning on a step the cancelled stream never settled', () => {
    render(<ChatSteps steps={[step('search', 'in_progress')]} expandByDefault />);
    expect(screen.getByText('search')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('spins only on the step still running, not on finished siblings', () => {
    render(
      <ChatSteps
        steps={[
          { ...step('search', 'complete'), index: 0 },
          { ...step('summarize', 'in_progress'), index: 1 },
        ]}
        streaming
      />,
    );
    expect(screen.getByRole('status', { name: 'summarize running' })).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'search running' })).not.toBeInTheDocument();
  });
});

describe('ChatSteps running step', () => {
  /** Expanded, so a nested spinner is actually rendered. */
  const openTree = (steps: ChatStep[]) =>
    render(<ChatSteps steps={steps} streaming expandByDefault />);

  it('stops a parent spinning once every child has settled', () => {
    openTree([
      step('search', 'in_progress'),
      { ...step('fetch-clip', 'complete'), parentId: 'search' },
    ]);

    fireEvent.click(screen.getByRole('button', { name: /search/ }));
    expect(screen.getByText('fetch-clip')).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'search running' })).not.toBeInTheDocument();
  });

  it('spins the parent and its newest child while that child works', () => {
    openTree([
      step('search', 'in_progress'),
      { ...step('fetch-clip', 'in_progress'), parentId: 'search' },
    ]);

    fireEvent.click(screen.getByRole('button', { name: /search/ }));
    expect(screen.getByRole('status', { name: 'search running' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'fetch-clip running' })).toBeInTheDocument();
  });

  it('stops a step the agent moved past, even if it never settled', () => {
    // `Reasoning` is emitted in_progress on every delta and never completed.
    openTree([step('Reasoning', 'in_progress'), step('vss-search-archive', 'in_progress')]);

    expect(screen.queryByRole('status', { name: 'Reasoning running' })).not.toBeInTheDocument();
    expect(
      screen.getByRole('status', { name: 'vss-search-archive running' }),
    ).toBeInTheDocument();
  });

  it('does not spin a nested step under a superseded parent', () => {
    openTree([
      step('search', 'in_progress'),
      { ...step('fetch-clip', 'in_progress'), parentId: 'search' },
      step('summarize', 'in_progress'),
    ]);

    fireEvent.click(screen.getByRole('button', { name: /search/ }));
    expect(screen.getByText('fetch-clip')).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'fetch-clip running' })).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'summarize running' })).toBeInTheDocument();
  });
});

describe('ChatSteps default expansion', () => {
  const nestedSteps: ChatStep[] = [
    step('search', 'in_progress', { payload: '{"query":"forklift"}' }),
    { ...step('fetch-clip', 'in_progress'), parentId: 'search' },
  ];

  it('lists first-level steps while streaming without opening their nested content', () => {
    render(<ChatSteps steps={nestedSteps} streaming />);

    expect(screen.getByText('search')).toBeInTheDocument();
    expect(screen.queryByText('fetch-clip')).not.toBeInTheDocument();
    expect(screen.queryByText('{"query":"forklift"}')).not.toBeInTheDocument();
  });

  it('opens a first-level step only after an explicit click', () => {
    render(<ChatSteps steps={nestedSteps} streaming />);

    fireEvent.click(screen.getByRole('button', { name: /search/ }));
    expect(screen.getByText('fetch-clip')).toBeInTheDocument();
    expect(screen.getByText('{"query":"forklift"}')).toBeInTheDocument();
  });
});
