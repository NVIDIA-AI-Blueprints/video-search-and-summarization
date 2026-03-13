// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { FilterControls } from '../../lib-src/components/FilterControls';
import { VLM_VERDICT, VlmVerdict } from '../../lib-src/types';

jest.mock('@nemo-agent-toolkit/ui');

const defaultProps = {
  isDark: false,
  vlmVerified: true,
  vlmVerdict: VLM_VERDICT.ALL as VlmVerdict,
  timeWindow: 10,
  timeFormat: 'local' as const,
  showCustomTimeInput: false,
  customTimeValue: '',
  customTimeError: '',
  uniqueValues: {
    sensors: ['Cam-A', 'Cam-B'],
    alertTypes: ['Tailgating', 'Loitering'],
    alertTriggered: ['Motion', 'Zone'],
  },
  loading: false,
  autoRefreshEnabled: false,
  autoRefreshInterval: 5000,
  onVlmVerifiedChange: jest.fn(),
  onVlmVerdictChange: jest.fn(),
  onTimeWindowChange: jest.fn(),
  onTimeFormatChange: jest.fn(),
  onCustomTimeValueChange: jest.fn(),
  onCustomTimeApply: jest.fn(),
  onCustomTimeCancel: jest.fn(),
  onOpenCustomTime: jest.fn(),
  onAddFilter: jest.fn(),
  onRefresh: jest.fn(),
  onAutoRefreshToggle: jest.fn(),
  onAutoRefreshIntervalChange: jest.fn(),
};

describe('FilterControls', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders without crashing', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByText('VLM Verified')).toBeInTheDocument();
  });

  it('renders VLM Verified toggle', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByText('VLM Verified')).toBeInTheDocument();
  });

  it('calls onVlmVerifiedChange when toggle is clicked', () => {
    const onVlmVerifiedChange = jest.fn();
    render(<FilterControls {...defaultProps} onVlmVerifiedChange={onVlmVerifiedChange} />);

    const toggleButtons = document.querySelectorAll('button');
    const toggleButton = Array.from(toggleButtons).find((btn) =>
      btn.className.includes('rounded-full')
    );
    expect(toggleButton).toBeTruthy();
    fireEvent.click(toggleButton!);
    expect(onVlmVerifiedChange).toHaveBeenCalledWith(false);
  });

  it('shows Verdict dropdown when vlmVerified is true', () => {
    render(<FilterControls {...defaultProps} vlmVerified={true} />);
    expect(screen.getByText('Verdict:')).toBeInTheDocument();
  });

  it('hides Verdict dropdown when vlmVerified is false', () => {
    render(<FilterControls {...defaultProps} vlmVerified={false} />);
    expect(screen.queryByText('Verdict:')).not.toBeInTheDocument();
  });

  it('renders Period label and time window selector', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByText('Period:')).toBeInTheDocument();
  });

  it('renders sensor filter dropdown with options', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByText('Sensor...')).toBeInTheDocument();
  });

  it('renders alert type filter dropdown', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByText('Alert Type...')).toBeInTheDocument();
  });

  it('renders alert triggered filter dropdown', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByText('Alert Triggered...')).toBeInTheDocument();
  });

  it('calls onAddFilter when a sensor is selected', () => {
    const onAddFilter = jest.fn();
    render(<FilterControls {...defaultProps} onAddFilter={onAddFilter} />);

    const sensorSelect = screen.getByDisplayValue('Sensor...');
    fireEvent.change(sensorSelect, { target: { value: 'Cam-A' } });

    expect(onAddFilter).toHaveBeenCalledWith('sensors', 'Cam-A');
  });

  it('calls onAddFilter when an alert type is selected', () => {
    const onAddFilter = jest.fn();
    render(<FilterControls {...defaultProps} onAddFilter={onAddFilter} />);

    const alertTypeSelect = screen.getByDisplayValue('Alert Type...');
    fireEvent.change(alertTypeSelect, { target: { value: 'Tailgating' } });

    expect(onAddFilter).toHaveBeenCalledWith('alertTypes', 'Tailgating');
  });

  it('calls onRefresh when refresh button is clicked', () => {
    const onRefresh = jest.fn();
    render(<FilterControls {...defaultProps} onRefresh={onRefresh} />);

    const refreshButton = screen.getByTitle('Refresh now');
    fireEvent.click(refreshButton);

    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('calls onTimeWindowChange when a predefined time is selected', () => {
    const onTimeWindowChange = jest.fn();
    render(<FilterControls {...defaultProps} onTimeWindowChange={onTimeWindowChange} />);

    const selects = document.querySelectorAll('select');
    const periodSelect = Array.from(selects).find((s) =>
      Array.from(s.options).some((o) => o.text === '10m')
    );
    expect(periodSelect).toBeTruthy();
    fireEvent.change(periodSelect!, { target: { value: '60' } });
    expect(onTimeWindowChange).toHaveBeenCalledWith(60);
  });

  it('calls onOpenCustomTime when Custom is selected', () => {
    const onOpenCustomTime = jest.fn();
    render(<FilterControls {...defaultProps} onOpenCustomTime={onOpenCustomTime} />);

    const selects = document.querySelectorAll('select');
    const periodSelect = Array.from(selects).find((s) =>
      Array.from(s.options).some((o) => o.text === 'Custom')
    );
    expect(periodSelect).toBeTruthy();
    fireEvent.change(periodSelect!, { target: { value: '-1' } });
    expect(onOpenCustomTime).toHaveBeenCalledTimes(1);
  });

  it('shows auto-refresh indicator when enabled', () => {
    render(<FilterControls {...defaultProps} autoRefreshEnabled={true} />);
    // When enabled, there's a pulse indicator dot
    const pulseDot = document.querySelector('.animate-pulse');
    expect(pulseDot).toBeInTheDocument();
  });

  it('does not show auto-refresh indicator when disabled', () => {
    render(<FilterControls {...defaultProps} autoRefreshEnabled={false} />);
    const pulseDot = document.querySelector('.animate-pulse');
    expect(pulseDot).not.toBeInTheDocument();
  });

  it('renders with dark theme', () => {
    render(<FilterControls {...defaultProps} isDark={true} />);
    expect(screen.getByText('VLM Verified')).toBeInTheDocument();
  });
});
