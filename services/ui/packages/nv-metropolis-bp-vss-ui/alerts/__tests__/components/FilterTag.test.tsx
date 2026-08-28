// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { AlertsViewFilterControls } from '../../lib-src/components/AlertsSidebarControls';
import { VLM_VERDICT } from '../../lib-src/types';

jest.mock('@nemo-agent-toolkit/ui');

const defaultProps = {
  isDark: false,
  vlmVerified: true,
  vlmVerdict: VLM_VERDICT.ALL,
  uniqueValues: {
    sensors: ['Cam-A', 'Cam-B'],
    alertTypes: ['Tailgating', 'Loitering'],
    alertTriggered: ['Motion', 'Zone'],
  },
  onVlmVerifiedChange: jest.fn(),
  onVlmVerdictChange: jest.fn(),
  onAddFilter: jest.fn(),
  activeFilters: {
    sensors: new Set<string>(['Cam-A']),
    alertTypes: new Set<string>(),
    alertTriggered: new Set<string>(),
  },
  onRemoveFilter: jest.fn(),
  onClearAllFilters: jest.fn(),
  timeWindow: 10,
  showCustomTimeInput: false,
  customTimeValue: '',
  customTimeError: '',
  onTimeWindowChange: jest.fn(),
  onCustomTimeValueChange: jest.fn(),
  onCustomTimeApply: jest.fn(),
  onCustomTimeCancel: jest.fn(),
  onOpenCustomTime: jest.fn(),
  fetchSize: 100,
  onFetchSizeChange: jest.fn(),
};

describe('Filter tags in AlertsViewFilterControls', () => {
  it('renders the filter text', () => {
    render(<AlertsViewFilterControls {...defaultProps} />);
    expect(screen.getByText('Cam-A', { selector: 'span' })).toBeInTheDocument();
  });

  it('calls onRemoveFilter with type and filter when close button is clicked', () => {
    const onRemoveFilter = jest.fn();
    render(<AlertsViewFilterControls {...defaultProps} onRemoveFilter={onRemoveFilter} />);

    const button = screen.getByLabelText('Remove filter Cam-A');
    fireEvent.click(button);

    expect(onRemoveFilter).toHaveBeenCalledWith('sensors', 'Cam-A');
  });

  it('applies color styles', () => {
    const { container } = render(<AlertsViewFilterControls {...defaultProps} />);
    const tag = screen.getByTestId('alerts-filter-tag-sensor-cam-a');
    const style = tag.getAttribute('style') || '';
    expect(style).toContain('background-color');
    expect(style).toContain('border-color');
    expect(style).toContain('color');
    expect(container.querySelector('[data-testid="alerts-filter-tags"]')).toBeTruthy();
  });

  it('renders different filter types', () => {
    const { rerender } = render(
      <AlertsViewFilterControls {...defaultProps} />
    );
    expect(screen.getByText('Cam-A', { selector: 'span' })).toBeInTheDocument();

    rerender(
      <AlertsViewFilterControls
        {...defaultProps}
        activeFilters={{
          sensors: new Set<string>(),
          alertTypes: new Set<string>(['Tailgating']),
          alertTriggered: new Set<string>(),
        }}
      />
    );
    expect(screen.getByText('Tailgating', { selector: 'span' })).toBeInTheDocument();
  });
});
