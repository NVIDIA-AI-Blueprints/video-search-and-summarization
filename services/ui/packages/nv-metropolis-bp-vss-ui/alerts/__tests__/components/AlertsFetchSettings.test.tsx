// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AlertsFetchSettings } from '../../lib-src/components/AlertsFetchSettings';

jest.mock('@nemo-agent-toolkit/ui');

jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Button: React.forwardRef(({ children, kind, ...rest }: any, ref: any) =>
      React.createElement(
        'button',
        { ...rest, ref, 'data-kind': kind, 'data-foundation': 'Button' },
        children,
      ),
    ),
    TextInput: React.forwardRef(({ onValueChange, onKeyDown, onKeyPress, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        'data-foundation': 'TextInput',
        onChange: (e: any) => onValueChange?.(e.target.value),
        onKeyDown: onKeyDown || onKeyPress,
      }),
    ),
  };
});

const defaultProps = {
  isOpen: true,
  isDark: false,
  onClose: jest.fn(),
  timeWindow: 10,
  onTimeWindowChange: jest.fn(),
  showCustomTimeInput: false,
  customTimeValue: '',
  customTimeError: '',
  onCustomTimeValueChange: jest.fn(),
  onCustomTimeApply: jest.fn(),
  onCustomTimeCancel: jest.fn(),
  onOpenCustomTime: jest.fn(),
  fetchSize: 500,
  onFetchSizeChange: jest.fn(),
};

describe('AlertsFetchSettings', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  // --- Visibility ---

  it('returns null when isOpen is false', () => {
    const { container } = render(
      <AlertsFetchSettings {...defaultProps} isOpen={false} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('renders when isOpen is true', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    expect(screen.getByText('Alerts Settings')).toBeInTheDocument();
  });

  // --- Sections and labels ---

  it('renders Query range section with hint', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    expect(screen.getByText('Query range')).toBeInTheDocument();
    expect(screen.getByText('How far back to fetch alerts')).toBeInTheDocument();
  });

  it('renders Fetch size section with hint and warning', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    expect(screen.getByText('Fetch size')).toBeInTheDocument();
    expect(screen.getByText('Max alerts per API call')).toBeInTheDocument();
    expect(screen.getByText('Higher values may be slower')).toBeInTheDocument();
  });

  // --- Query range select ---

  it('calls onTimeWindowChange when a preset period is selected', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    const select = document.getElementById('settings-period-select')!;
    fireEvent.change(select, { target: { value: '60' } });
    expect(defaultProps.onTimeWindowChange).toHaveBeenCalledWith(60);
  });

  it('calls onOpenCustomTime when Custom period is selected', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    const select = document.getElementById('settings-period-select')!;
    fireEvent.change(select, { target: { value: '-1' } });
    expect(defaultProps.onOpenCustomTime).toHaveBeenCalledTimes(1);
  });

  it('shows current custom time window as option when not in presets', () => {
    render(<AlertsFetchSettings {...defaultProps} timeWindow={45} />);
    expect(screen.getByText('45m')).toBeInTheDocument();
  });

  // --- Fetch size ---

  it('calls onFetchSizeChange when a preset fetch size is selected', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    const selects = screen.getAllByDisplayValue('500');
    const fetchSizeSelect = selects[0];
    fireEvent.change(fetchSizeSelect, { target: { value: '1000' } });
    expect(defaultProps.onFetchSizeChange).toHaveBeenCalledWith(1000);
  });

  it('opens custom fetch size input when "Custom..." is selected', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    const selects = screen.getAllByDisplayValue('500');
    fireEvent.change(selects[0], { target: { value: '-1' } });

    expect(screen.getByPlaceholderText('10 – 5000')).toBeInTheDocument();
  });

  it('applies valid custom fetch size via OK button', () => {
    const onFetchSizeChange = jest.fn();
    render(
      <AlertsFetchSettings {...defaultProps} onFetchSizeChange={onFetchSizeChange} />,
    );

    const selects = screen.getAllByDisplayValue('500');
    fireEvent.change(selects[0], { target: { value: '-1' } });

    const input = screen.getByPlaceholderText('10 – 5000');
    fireEvent.change(input, { target: { value: '350' } });

    const okButtons = screen.getAllByText('OK');
    fireEvent.click(okButtons[0]);
    expect(onFetchSizeChange).toHaveBeenCalledWith(350);
  });

  it('shows error for invalid custom fetch size', () => {
    render(<AlertsFetchSettings {...defaultProps} />);

    const selects = screen.getAllByDisplayValue('500');
    fireEvent.change(selects[0], { target: { value: '-1' } });

    const input = screen.getByPlaceholderText('10 – 5000');
    fireEvent.change(input, { target: { value: '5' } });

    const okButtons = screen.getAllByText('OK');
    fireEvent.click(okButtons[0]);

    expect(screen.getByText('Enter a number between 10 and 5000')).toBeInTheDocument();
  });

  it('shows custom fetch size in dropdown when not in presets', () => {
    render(<AlertsFetchSettings {...defaultProps} fetchSize={777} />);
    expect(screen.getByText('777 (custom)')).toBeInTheDocument();
  });

  it('applies custom fetch size via Enter key', () => {
    const onFetchSizeChange = jest.fn();
    render(
      <AlertsFetchSettings {...defaultProps} onFetchSizeChange={onFetchSizeChange} />,
    );

    const selects = screen.getAllByDisplayValue('500');
    fireEvent.change(selects[0], { target: { value: '-1' } });

    const input = screen.getByPlaceholderText('10 – 5000');
    fireEvent.change(input, { target: { value: '2500' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onFetchSizeChange).toHaveBeenCalledWith(2500);
  });

  // --- Close behaviour ---

  it('calls onClose when close button is clicked', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    const headerCloseBtn = screen.getAllByText('✕').find(
      (btn) => btn.tagName === 'BUTTON' && !btn.getAttribute('data-foundation'),
    );
    fireEvent.click(headerCloseBtn!);
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose on Escape key', () => {
    render(<AlertsFetchSettings {...defaultProps} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose on click outside', () => {
    render(
      <div>
        <div data-testid="outside">Outside</div>
        <AlertsFetchSettings {...defaultProps} />
      </div>,
    );

    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  // --- Dark mode ---

  it('renders with dark theme styles', () => {
    const { container } = render(
      <AlertsFetchSettings {...defaultProps} isDark={true} />,
    );
    expect(container.querySelector('.bg-black')).toBeInTheDocument();
  });

  it('renders with light theme styles', () => {
    const { container } = render(
      <AlertsFetchSettings {...defaultProps} isDark={false} />,
    );
    expect(container.querySelector('.bg-white')).toBeInTheDocument();
  });
});
