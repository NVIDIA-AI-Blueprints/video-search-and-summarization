// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { CustomTimeInput } from '../../lib-src/components/CustomTimeInput';

jest.mock('@nemo-agent-toolkit/ui');

const defaultProps = {
  isOpen: true,
  timeWindow: 10,
  customTimeValue: '',
  customTimeError: '',
  isDark: false,
  onTimeValueChange: jest.fn(),
  onApply: jest.fn(),
  onCancel: jest.fn(),
};

describe('CustomTimeInput', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns null when isOpen is false', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} isOpen={false} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders when isOpen is true', () => {
    render(<CustomTimeInput {...defaultProps} />);
    expect(screen.getByText('Custom Period')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('e.g. 40m, 4h, 1d, 1w, 1M, 1y')).toBeInTheDocument();
  });

  it('renders Apply and Cancel buttons', () => {
    render(<CustomTimeInput {...defaultProps} />);
    expect(screen.getByText('Apply')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('displays current time window in header', () => {
    render(<CustomTimeInput {...defaultProps} timeWindow={60} />);
    expect(screen.getByText(/1h ago → now/)).toBeInTheDocument();
  });

  it('calls onTimeValueChange when input changes', () => {
    const onTimeValueChange = jest.fn();
    render(<CustomTimeInput {...defaultProps} onTimeValueChange={onTimeValueChange} />);

    const input = screen.getByPlaceholderText('e.g. 40m, 4h, 1d, 1w, 1M, 1y');
    fireEvent.change(input, { target: { value: '2h' } });

    expect(onTimeValueChange).toHaveBeenCalledWith('2h');
  });

  it('calls onApply when Apply button is clicked', () => {
    const onApply = jest.fn();
    render(<CustomTimeInput {...defaultProps} customTimeValue="30m" onApply={onApply} />);

    fireEvent.click(screen.getByText('Apply'));
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when Cancel button is clicked', () => {
    const onCancel = jest.fn();
    render(<CustomTimeInput {...defaultProps} onCancel={onCancel} />);

    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when close (✕) button is clicked', () => {
    const onCancel = jest.fn();
    render(<CustomTimeInput {...defaultProps} onCancel={onCancel} />);

    fireEvent.click(screen.getByText('✕'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('disables Apply button when there is an error', () => {
    render(<CustomTimeInput {...defaultProps} customTimeError="Invalid format" customTimeValue="bad" />);

    const applyButton = screen.getByText('Apply');
    expect(applyButton).toBeDisabled();
  });

  it('disables Apply button when input is empty', () => {
    render(<CustomTimeInput {...defaultProps} customTimeValue="" />);

    const applyButton = screen.getByText('Apply');
    expect(applyButton).toBeDisabled();
  });

  it('enables Apply button when input is valid', () => {
    render(<CustomTimeInput {...defaultProps} customTimeValue="2h" customTimeError="" />);

    const applyButton = screen.getByText('Apply');
    expect(applyButton).not.toBeDisabled();
  });

  it('displays error message', () => {
    render(<CustomTimeInput {...defaultProps} customTimeError="Invalid format" />);
    expect(screen.getByText('Invalid format')).toBeInTheDocument();
  });

  it('calls onApply on Enter key when valid', () => {
    const onApply = jest.fn();
    render(<CustomTimeInput {...defaultProps} customTimeValue="1h" onApply={onApply} />);

    const input = screen.getByPlaceholderText('e.g. 40m, 4h, 1d, 1w, 1M, 1y');
    fireEvent.keyPress(input, { key: 'Enter', charCode: 13 });

    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it('does not call onApply on Enter when there is an error', () => {
    const onApply = jest.fn();
    render(
      <CustomTimeInput
        {...defaultProps}
        customTimeValue="bad"
        customTimeError="Invalid"
        onApply={onApply}
      />
    );

    const input = screen.getByPlaceholderText('e.g. 40m, 4h, 1d, 1w, 1M, 1y');
    fireEvent.keyPress(input, { key: 'Enter', charCode: 13 });

    expect(onApply).not.toHaveBeenCalled();
  });

  it('calls onCancel on Escape key', () => {
    const onCancel = jest.fn();
    render(<CustomTimeInput {...defaultProps} onCancel={onCancel} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('shows max time limit info when provided', () => {
    render(<CustomTimeInput {...defaultProps} maxTimeLimitInMinutes={1440} />);
    expect(screen.getByText(/Max period: 1d/)).toBeInTheDocument();
  });

  it('shows unlimited max period when limit is 0', () => {
    render(<CustomTimeInput {...defaultProps} maxTimeLimitInMinutes={0} />);
    expect(screen.getByText(/Max period: Unlimited/)).toBeInTheDocument();
  });

  it('renders format guidance text', () => {
    render(<CustomTimeInput {...defaultProps} />);
    expect(screen.getByText('Format:')).toBeInTheDocument();
    expect(screen.getByText(/40m • 2h • 1d • 1w • 1M • 1y/)).toBeInTheDocument();
  });

  it('renders with dark theme', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} isDark={true} />);
    expect(container.querySelector('.bg-gray-800')).toBeInTheDocument();
  });
});
