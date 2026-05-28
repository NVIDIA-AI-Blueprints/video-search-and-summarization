// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen } from '@testing-library/react';
import HomePage from '../../pages/index';

describe('HomePage', () => {
  it('renders the NemoAgentToolkitApp component', () => {
    render(<HomePage />);
    expect(screen.getByTestId('nemo-agent-toolkit-app')).toBeInTheDocument();
  });
});
