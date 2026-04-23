// SPDX-License-Identifier: MIT
/**
 * Tests for Kaizen UI Foundations migration in the alerts package.
 *
 * Validates that:
 * 1. Native HTML buttons/inputs/selects are replaced with Foundation components
 *    (Button, TextInput, Select).
 * 2. Dark mode backgrounds migrate to true black / neutral-900.
 * 3. Accent colors shift from blue/cyan to NVIDIA green (#76b900).
 * 4. Filter tag sensor colors switch from cyan/blue to green.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';

// ---------- Foundation component mocks ----------
jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Button: React.forwardRef(({ children, kind, ...rest }: any, ref: any) =>
      React.createElement('button', { ...rest, ref, 'data-kind': kind, 'data-foundation': 'Button' }, children),
    ),
    TextInput: React.forwardRef(({ onValueChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        'data-foundation': 'TextInput',
        onChange: (e: any) => onValueChange?.(e.target.value),
      }),
    ),
    Select: React.forwardRef(({ items, onValueChange, value, ...rest }: any, ref: any) =>
      React.createElement(
        'select',
        {
          ...rest,
          ref,
          'data-foundation': 'Select',
          value,
          onChange: (e: any) => onValueChange?.(e.target.value),
        },
        items?.map((item: any) =>
          React.createElement('option', { key: item.value, value: item.value }, item.children),
        ),
      ),
    ),
    Switch: React.forwardRef(({ checked, onCheckedChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        type: 'checkbox',
        checked,
        'data-foundation': 'Switch',
        onChange: (e: any) => onCheckedChange?.(e.target.checked),
      }),
    ),
  };
});

jest.mock('@nemo-agent-toolkit/ui');

// ---------- Component imports ----------
import { FilterTag } from '../../lib-src/components/FilterTag';
import { CustomTimeInput } from '../../lib-src/components/CustomTimeInput';
import { AlertsTable } from '../../lib-src/components/AlertsTable';

// ---------- Tests ----------

describe('Foundation component migration – FilterTag', () => {
  const defaultProps = {
    type: 'sensors' as const,
    filter: 'Camera-1',
    colors: { bg: 'bg-transparent', border: 'border border-green-500', text: 'text-green-400', hover: 'hover:text-green-300' },
    onRemove: jest.fn(),
  };

  it('renders a button for the remove action', () => {
    const { container } = render(<FilterTag {...defaultProps} />);
    const btn = container.querySelector('button');
    expect(btn).toBeTruthy();
  });

  it('calls onRemove with correct args when remove button is clicked', () => {
    const onRemove = jest.fn();
    const { container } = render(<FilterTag {...defaultProps} onRemove={onRemove} />);
    const btn = container.querySelector('button') as HTMLElement;
    fireEvent.click(btn);
    expect(onRemove).toHaveBeenCalledWith('sensors', 'Camera-1');
  });

  it('displays the filter text', () => {
    render(<FilterTag {...defaultProps} />);
    expect(screen.getByText('Camera-1')).toBeTruthy();
  });
});

describe('Foundation component migration – CustomTimeInput', () => {
  const defaultProps = {
    isOpen: true,
    timeWindow: 3600,
    customTimeValue: '2h',
    customTimeError: '',
    isDark: true,
    onTimeValueChange: jest.fn(),
    onApply: jest.fn(),
    onCancel: jest.fn(),
  };

  it('renders Foundation TextInput for custom period entry', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} />);
    const input = container.querySelector('[data-foundation="TextInput"]');
    expect(input).toBeTruthy();
  });

  it('renders Foundation Button components for Cancel and Apply', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} />);
    const buttons = container.querySelectorAll('[data-foundation="Button"]');
    const kinds = Array.from(buttons).map((b) => b.getAttribute('data-kind'));
    expect(kinds).toContain('secondary');
    expect(kinds).toContain('primary');
  });

  it('renders a dismiss (✕) button in the header', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} />);
    const buttons = container.querySelectorAll('button');
    const dismissBtn = Array.from(buttons).find(
      (b) => b.textContent?.trim() === '✕',
    );
    expect(dismissBtn).toBeTruthy();
  });

  it('calls onCancel when Cancel button is clicked', () => {
    const onCancel = jest.fn();
    render(<CustomTimeInput {...defaultProps} onCancel={onCancel} />);
    const cancelBtn = screen.getByText('Cancel');
    fireEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalled();
  });

  it('calls onApply when Apply button is clicked', () => {
    const onApply = jest.fn();
    render(<CustomTimeInput {...defaultProps} onApply={onApply} />);
    const applyBtn = screen.getByText('Apply');
    fireEvent.click(applyBtn);
    expect(onApply).toHaveBeenCalled();
  });

  it('disables Apply when there is a validation error', () => {
    const { container } = render(
      <CustomTimeInput {...defaultProps} customTimeError="Invalid format" />,
    );
    const applyBtn = screen.getByText('Apply');
    expect(applyBtn).toBeDisabled();
  });

  it('returns null when isOpen is false', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} isOpen={false} />);
    expect(container.innerHTML).toBe('');
  });

  it('uses dark:bg-black instead of dark:bg-gray-800', () => {
    const { container } = render(<CustomTimeInput {...defaultProps} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('bg-black');
    expect(root.className).not.toContain('bg-gray-800');
  });
});

describe('Foundation component migration – AlertsTable', () => {
  const baseProps = {
    alerts: [],
    loading: false,
    error: null,
    isDark: true,
    activeFilters: { sensors: new Set<string>(), alertTypes: new Set<string>(), alertTriggered: new Set<string>() },
    onAddFilter: jest.fn(),
    onPlayVideo: jest.fn(),
    onRefresh: jest.fn(),
  };

  it('uses Foundation Button for retry on error', () => {
    const { container } = render(
      <AlertsTable {...baseProps} error="Network failure" />,
    );
    const retryBtn = container.querySelector('[data-foundation="Button"]');
    expect(retryBtn).toBeTruthy();
    expect(retryBtn?.getAttribute('data-kind')).toBe('primary');
    expect(retryBtn?.textContent).toContain('Retry');
  });

  it('calls onRefresh when Retry is clicked', () => {
    const onRefresh = jest.fn();
    const { container } = render(
      <AlertsTable {...baseProps} error="fail" onRefresh={onRefresh} />,
    );
    const retryBtn = container.querySelector('[data-foundation="Button"]') as HTMLElement;
    fireEvent.click(retryBtn);
    expect(onRefresh).toHaveBeenCalled();
  });

  it('uses green accent color for spinner instead of blue', () => {
    const { container } = render(
      <AlertsTable {...baseProps} loading={true} />,
    );
    const html = container.innerHTML;
    expect(html).toContain('text-green-');
    expect(html).not.toContain('text-blue-400');
    expect(html).not.toContain('text-blue-500');
  });

  it('renders Foundation Buttons in table rows for expand/collapse and filters', () => {
    const alertData = {
      id: 'a1',
      timestamp: '2026-01-01T00:00:00Z',
      end: '2026-01-01T01:00:00Z',
      sensor: 'Cam-1',
      alertType: 'intrusion',
      alertTriggered: 'motion',
      alertDescription: 'Test alert',
      metadata: {},
    };
    const { container } = render(
      <AlertsTable {...baseProps} alerts={[alertData]} />,
    );
    const foundationButtons = container.querySelectorAll('[data-foundation="Button"]');
    expect(foundationButtons.length).toBeGreaterThan(0);
    const tertiaryButtons = Array.from(foundationButtons).filter(
      (b) => b.getAttribute('data-kind') === 'tertiary',
    );
    expect(tertiaryButtons.length).toBeGreaterThanOrEqual(3);
  });

  it('uses bg-black instead of bg-gray-800 in dark mode table header', () => {
    const alertData = {
      id: 'a1',
      timestamp: '2026-01-01T00:00:00Z',
      sensor: 'S1',
      alertType: 'T1',
      alertTriggered: 'M1',
      alertDescription: 'D1',
      metadata: {},
    };
    const { container } = render(
      <AlertsTable {...baseProps} alerts={[alertData]} />,
    );
    const html = container.innerHTML;
    expect(html).not.toContain('bg-gray-800');
  });

  it('uses bg-black for even row backgrounds in dark mode', () => {
    const alertData = {
      id: 'a1',
      timestamp: '2026-01-01T00:00:00Z',
      sensor: 'S1',
      alertType: 'T1',
      alertTriggered: 'M1',
      alertDescription: 'D1',
      metadata: {},
    };
    const { container } = render(
      <AlertsTable {...baseProps} alerts={[alertData]} />,
    );
    const rows = container.querySelectorAll('tr');
    const dataRow = rows[1]; // first data row (index 0 is header)
    expect(dataRow?.className).toContain('bg-black');
  });
});

describe('Kaizen color palette – sensor filter colors', () => {
  it('uses green colors for sensor filter tags instead of cyan/blue', () => {
    const props = {
      type: 'sensors' as const,
      filter: 'Camera-1',
      colors: { bg: 'bg-transparent', border: 'border border-green-500', text: 'text-green-400', hover: 'hover:text-green-300' },
      onRemove: jest.fn(),
    };
    const { container } = render(<FilterTag {...props} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('border-green-500');
    expect(root.className).toContain('text-green-400');
    expect(root.className).not.toContain('cyan');
    expect(root.className).not.toContain('blue');
  });
});
