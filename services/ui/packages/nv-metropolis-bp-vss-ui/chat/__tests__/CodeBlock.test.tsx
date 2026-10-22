// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { act, fireEvent, render, screen } from '@testing-library/react';
import { copyToClipboard } from 'common';
import React from 'react';

import { CodeBlock } from '../lib-src/markdown/CodeBlock';
import { CODE_HIGHLIGHT_MAX_CHARS } from '../lib-src/markdown/highlight';

jest.mock('common', () => ({
  ...jest.requireActual('common'),
  copyToClipboard: jest.fn().mockResolvedValue(true),
}));

jest.mock('react-syntax-highlighter', () => ({
  Prism: ({ children }: { children: string }) => <pre data-testid="syntax-highlighter">{children}</pre>,
}));

jest.mock('react-syntax-highlighter/dist/esm/styles/prism/one-dark', () => ({
  default: {},
}));

describe('CodeBlock highlighting', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('does not load the highlighter for a large JSON payload', () => {
    const value = JSON.stringify({ blob: 'x'.repeat(CODE_HIGHLIGHT_MAX_CHARS) });
    const { container, queryByTestId } = render(<CodeBlock language="json" value={value} />);

    expect(container.querySelector('[data-highlighted="false"]')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(2_000);
    });
    expect(container.querySelector('[data-highlighted="false"]')).toBeInTheDocument();
    expect(queryByTestId('syntax-highlighter')).not.toBeInTheDocument();
  });

  it('attempts to highlight a small JSON payload once it is stable', () => {
    const { container } = render(<CodeBlock language="json" value='{"query":"forklift"}' />);

    expect(container.querySelector('[data-highlighted="false"]')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(container.querySelector('[data-highlighted="true"]')).toBeInTheDocument();
  });
});

describe('CodeBlock copy', () => {
  beforeEach(() => {
    (copyToClipboard as jest.Mock).mockClear();
  });

  it('copies answer python as written, not a JSON rewrite', async () => {
    const python = "{'ok': True, 'value': None}";
    render(<CodeBlock language="python" value={python} />);

    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByText(python)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByLabelText('Copy code'));
    });
    expect(copyToClipboard).toHaveBeenCalledWith(python);
  });
});
