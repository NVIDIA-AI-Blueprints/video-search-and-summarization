// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { render, screen } from '@testing-library/react';
import React from 'react';

import { ChatSteps } from '../lib-src/ChatSteps';
import type { ChatStep } from '../lib-src/types';

const step = (id: string, status: ChatStep['status'], index = 0): ChatStep => ({
  id,
  name: id,
  status,
  index,
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

  it('spins only on the step still running, not on finished siblings', () => {
    render(
      <ChatSteps
        steps={[step('search', 'complete', 0), step('summarize', 'in_progress', 1)]}
        streaming
      />,
    );
    expect(screen.getByRole('status', { name: 'summarize running' })).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'search running' })).not.toBeInTheDocument();
  });
});
